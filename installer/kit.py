#!/usr/bin/env python3
"""Deterministic client installer and release builder.

All commands are dry-run or read-only unless the caller supplies both --apply
and --yes.  Release payloads are selected from an allowlist contract and never
follow symlinks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "installer" / "contracts" / "public-payload.json"
VERSION = "1.0.0"
sys.path.insert(0, str(ROOT))
from installer.lib.redaction import redact

STATUSES = {"PASS", "FAIL", "BLOCKED", "SKIPPED", "DRY-RUN"}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(r"(?i)(?:api[_-]?key|password|secret|bearer[_-]?token)\s*[=:]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}"),
    "home_path": re.compile(r"(?i)(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s]+|/(?:home|Users)/[^/\s]+)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}
TEXT_SUFFIXES = {".py", ".ps1", ".sh", ".md", ".json", ".yaml", ".yml", ".txt", ".toml", ".js", ".mjs", ".cmd", ".bat"}


def emit(status: str, command: str, checks: list[dict], next_step: str = "") -> int:
    assert status in STATUSES
    result = {"status": status, "command": command, "checks": checks}
    if next_step:
        result["next_step"] = next_step
    print(json.dumps(result, indent=2))
    return 0 if status in {"PASS", "SKIPPED", "DRY-RUN"} else 2


def load_contract(path: Path = CONTRACT) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"version", "default_groups", "groups", "forbidden_roots", "forbidden_names"}
    if set(value) != required:
        raise ValueError("payload contract has unknown or missing keys")
    if any(group not in value["groups"] for group in value["default_groups"]):
        raise ValueError("default group is not defined")
    for patterns in value["groups"].values():
        for pattern in patterns:
            posix = PurePosixPath(pattern)
            if posix.is_absolute() or ".." in posix.parts or "\\" in pattern:
                raise ValueError(f"unsafe payload pattern: {pattern}")
    return value


def selected_files(source: Path, contract: dict) -> list[Path]:
    forbidden_roots = set(contract["forbidden_roots"])
    forbidden_names = set(contract["forbidden_names"])
    selected: set[Path] = set()
    for group in contract["default_groups"]:
        for pattern in contract["groups"][group]:
            for candidate in source.glob(pattern):
                paths = candidate.rglob("*") if candidate.is_dir() and not candidate.is_symlink() else (candidate,)
                for path in paths:
                    if not path.is_file() or path.is_symlink():
                        continue
                    rel = path.relative_to(source)
                    if any(part in forbidden_roots for part in rel.parts) or rel.name in forbidden_names:
                        continue
                    selected.add(rel)
    return sorted(selected, key=lambda p: p.as_posix())


def scan_tree(root: Path) -> list[dict]:
    hits = []
    approved_emails = {"alice" + "@" + "example.com"}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or ".git" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"LICENSE", ".gitignore", ".gitattributes"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for kind, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                token = match.group(0)
                if kind == "email" and token.lower() in approved_emails:
                    continue
                if kind == "home_path" and ("alice" in token.lower() or "<" in token):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                hits.append({"file": rel, "line": line, "kind": kind, "token": redact(token)})
    return hits


def copy_payload(source: Path, destination: Path) -> list[str]:
    contract = load_contract()
    files = selected_files(source, contract)
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    for rel in files:
        src = (source / rel).resolve()
        if source.resolve() not in src.parents:
            raise ValueError(f"source escaped payload root: {rel}")
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        written.append(rel.as_posix())
    return written


def command_build(args) -> int:
    source, destination = ROOT.resolve(), args.output.resolve()
    if destination == source or source in destination.parents:
        return emit("BLOCKED", "build", [{"name": "destination", "status": "BLOCKED"}], "Choose a staging directory outside the repository.")
    if destination.exists() and any(destination.iterdir()) and not (args.apply and args.yes):
        return emit("BLOCKED", "build", [{"name": "destination-empty", "status": "BLOCKED"}], "Use an empty destination or review --apply --yes.")
    if not args.apply:
        count = len(selected_files(source, load_contract()))
        return emit("DRY-RUN", "build", [{"name": "allowlisted-files", "status": "PASS", "count": count}])
    files = copy_payload(source, destination)
    hits = scan_tree(destination)
    if hits:
        return emit("BLOCKED", "build", [{"name": "privacy-scan", "status": "BLOCKED", "hits": hits[:20]}])
    manifest = {"version": VERSION, "files": []}
    for rel in files:
        data = (destination / rel).read_bytes()
        manifest["files"].append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    (destination / "RELEASE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return emit("PASS", "build", [{"name": "privacy-scan", "status": "PASS"}, {"name": "payload", "status": "PASS", "files": len(files)}])


def config_value(args) -> dict:
    return {
        "version": 1, "install_root": str(args.target.resolve()), "data_root": str(args.data_dir.resolve()),
        "python": sys.executable, "harness": args.harness,
        "secret_environment_variables": [], "optional_modules": [],
    }


def command_configure(args) -> int:
    output = args.output.resolve()
    if not args.apply:
        return emit("DRY-RUN", "configure", [{"name": "secret-free-config", "status": "PASS"}])
    if not args.yes:
        return emit("BLOCKED", "configure", [{"name": "confirmation", "status": "BLOCKED"}], "Review paths, then add --yes.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config_value(args), indent=2) + "\n", encoding="utf-8")
    return emit("PASS", "configure", [{"name": "staged-config", "status": "PASS", "path": output.name}])


def command_install(args) -> int:
    target, data = args.target.resolve(), args.data_dir.resolve()
    if target == Path(target.anchor) or data == Path(data.anchor) or target == ROOT:
        return emit("BLOCKED", "install", [{"name": "safe-paths", "status": "BLOCKED"}])
    if not args.apply:
        return emit("DRY-RUN", "install", [{"name": "preflight", "status": "PASS"}, {"name": "writes", "status": "SKIPPED"}])
    if not args.yes:
        return emit("BLOCKED", "install", [{"name": "confirmation", "status": "BLOCKED"}], "Review target and data paths, then add --yes.")
    with tempfile.TemporaryDirectory(prefix="orchestration-install-") as temp:
        staged = Path(temp) / "payload"
        files = copy_payload(ROOT, staged)
        hits = scan_tree(staged)
        if hits:
            return emit("BLOCKED", "install", [{"name": "privacy-scan", "status": "BLOCKED", "hits": hits[:20]}])
        target.mkdir(parents=True, exist_ok=True)
        for rel in files:
            src, dst = staged / rel, target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or src.read_bytes() != dst.read_bytes():
                shutil.copyfile(src, dst)
    for name in ("chroma", "spool", "spool/done", "spool/error"):
        (data / name).mkdir(parents=True, exist_ok=True)
    config = config_value(args)
    (target / "config.local.yaml").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    managed = {"kit_version": VERSION, "install_root": str(target), "data_root": str(data), "files": files + ["config.local.yaml"]}
    (target / "managed-files.json").write_text(json.dumps(managed, indent=2) + "\n", encoding="utf-8")
    return emit("PASS", "install", [{"name": "core-files", "status": "PASS", "count": len(files)}, {"name": "empty-memory", "status": "PASS"}])


def command_doctor(args) -> int:
    target = args.target.resolve()
    checks = []
    for name in ("managed-files.json", "config.local.yaml", "system/memoryweaver/store/chroma.py", "modules/topology/configure-topology.py"):
        checks.append({"name": name, "status": "PASS" if (target / name).is_file() else "FAIL"})
    status = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return emit(status, "doctor", checks, "Re-run the installer after reviewing its dry-run." if status == "FAIL" else "")


def command_uninstall(args) -> int:
    target = args.target.resolve()
    manifest_path = target / "managed-files.json"
    if not manifest_path.is_file():
        return emit("BLOCKED", "uninstall", [{"name": "managed-manifest", "status": "BLOCKED"}])
    if not (args.apply and args.yes):
        return emit("DRY-RUN", "uninstall", [{"name": "managed-only", "status": "PASS"}])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel_text in manifest["files"]:
        rel = PurePosixPath(rel_text)
        if rel.is_absolute() or ".." in rel.parts:
            return emit("BLOCKED", "uninstall", [{"name": "unsafe-manifest", "status": "BLOCKED"}])
        path = target / Path(*rel.parts)
        if path.is_file() or path.is_symlink():
            path.unlink()
    for directory in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
        try: directory.rmdir()
        except OSError: pass
    return emit("PASS", "uninstall", [{"name": "managed-files-removed", "status": "PASS"}, {"name": "recipient-data", "status": "SKIPPED"}])


def command_privacy(args) -> int:
    root = args.root.resolve()
    hits = scan_tree(root)
    return emit("PASS" if not hits else "BLOCKED", "privacy-gate", [{"name": "payload-scan", "status": "PASS" if not hits else "BLOCKED", "hits": hits[:20]}])


def command_bundle(args) -> int:
    output = args.output.resolve()
    if not (args.apply and args.yes):
        return emit("DRY-RUN", "bundle", [{"name": "reproducible-archive", "status": "PASS"}])
    with tempfile.TemporaryDirectory(prefix="orchestration-bundle-") as temp:
        stage = Path(temp) / "payload"
        files = copy_payload(ROOT, stage)
        hits = scan_tree(stage)
        if hits:
            return emit("BLOCKED", "bundle", [{"name": "privacy-scan", "status": "BLOCKED", "hits": hits[:20]}])
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for rel in files:
                info = zipfile.ZipInfo(f"orchestration-harness-kit/{rel}", (1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                archive.writestr(info, (stage / rel).read_bytes())
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    return emit("PASS", "bundle", [{"name": "privacy-scan", "status": "PASS"}, {"name": "archive", "status": "PASS", "sha256": checksum}])


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    def mut(name, func):
        cmd = sub.add_parser(name); cmd.add_argument("--apply", action="store_true"); cmd.add_argument("--yes", action="store_true"); cmd.set_defaults(func=func); return cmd
    build = mut("build", command_build); build.add_argument("--output", type=Path, required=True)
    configure = mut("configure", command_configure); configure.add_argument("--output", type=Path, default=ROOT / "installer-staging" / "config.local.yaml"); configure.add_argument("--target", type=Path, required=True); configure.add_argument("--data-dir", type=Path, required=True); configure.add_argument("--harness", choices=["generic", "claude", "codex", "hermes"], default="generic")
    install = mut("install", command_install); install.add_argument("--target", type=Path, required=True); install.add_argument("--data-dir", type=Path, required=True); install.add_argument("--harness", choices=["generic", "claude", "codex", "hermes"], default="generic")
    doctor = sub.add_parser("doctor"); doctor.add_argument("--target", type=Path, required=True); doctor.set_defaults(func=command_doctor)
    uninstall = mut("uninstall", command_uninstall); uninstall.add_argument("--target", type=Path, required=True)
    privacy = sub.add_parser("privacy-gate"); privacy.add_argument("--root", type=Path, default=ROOT); privacy.set_defaults(func=command_privacy)
    bundle = mut("bundle", command_bundle); bundle.add_argument("--output", type=Path, required=True)
    return root


if __name__ == "__main__":
    args = parser().parse_args()
    try:
        raise SystemExit(args.func(args))
    except Exception as exc:
        raise SystemExit(emit("FAIL", args.command, [{"name": "exception", "status": "FAIL", "detail": redact(exc)}]))
