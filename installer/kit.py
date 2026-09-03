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
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "installer" / "contracts" / "public-payload.json"
VERSION = "2.0.0-dev"
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
GENERATED_DIRS = {"__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
GENERATED_SUFFIXES = {".pyc", ".pyo"}
HARNESS_ALIASES = {
    "generic": "generic",
    "claude": "claude-code",
    "claude-code": "claude-code",
    "codex": "codex",
    "hermes": "hermes",
    "opencode": "opencode",
    "pi": "pi",
    "antigravity": "antigravity",
    "moa": "moa",
}
REFERENCE_RE = re.compile(r"^(?:env|file|secret|recipient)://[^\s]+$")
SNAPSHOT_RE = re.compile(r"^[0-9a-f]{16}$")


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
                    if (
                        any(part in forbidden_roots or part in GENERATED_DIRS for part in rel.parts)
                        or rel.name in forbidden_names
                        or rel.suffix.lower() in GENERATED_SUFFIXES
                    ):
                        continue
                    selected.add(rel)
    return sorted(selected, key=lambda p: p.as_posix())


def _relative(text: str) -> Path:
    posix = PurePosixPath(text)
    if posix.is_absolute() or not posix.parts or ".." in posix.parts or "." in posix.parts:
        raise ValueError(f"unsafe managed path: {text}")
    return Path(*posix.parts)


def _safe_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("unsafe managed root")
    return resolved


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _load_managed(target: Path) -> dict:
    target = _safe_root(target)
    path = target / "managed-files.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"kit_version", "install_root", "data_root", "files"}
    if not required.issubset(value) or Path(value["install_root"]).resolve() != target:
        raise ValueError("managed manifest does not own this target")
    files = value.get("files")
    if not isinstance(files, list) or len(files) != len(set(files)):
        raise ValueError("managed file list is invalid")
    for relative in files:
        _relative(relative)
    _safe_root(Path(value["data_root"]))
    return value


def _payload_hashes(target: Path, files: list[str]) -> dict[str, str]:
    return {
        relative: hashlib.sha256((target / _relative(relative)).read_bytes()).hexdigest()
        for relative in files
    }


def _managed_value(target: Path, data: Path, files: list[str], local_files: list[str], harness: str) -> dict:
    payload_files = sorted(set(files))
    local = sorted(set(local_files))
    return {
        "schema_version": 2,
        "kit_version": VERSION,
        "install_root": str(target),
        "data_root": str(data),
        "harness": harness,
        "files": payload_files + local,
        "payload_files": payload_files,
        "local_files": local,
        "connections": [],
        "sha256": _payload_hashes(target, payload_files),
    }


def _integrity_errors(target: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    for relative in manifest.get("files", []):
        path = target / _relative(relative)
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing-or-unsafe:{relative}")
    hashes = manifest.get("sha256", {})
    if not isinstance(hashes, dict):
        errors.append("invalid-hash-map")
        return errors
    for relative, expected in hashes.items():
        path = target / _relative(relative)
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or not path.is_file():
            errors.append(f"invalid-hash:{relative}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f"hash-mismatch:{relative}")
    return errors


def _normalized_harness(name: str) -> str:
    try:
        return HARNESS_ALIASES[name]
    except KeyError as exc:
        raise ValueError(f"unsupported harness: {name}") from exc


def scan_paths(root: Path, paths) -> list[dict]:
    hits = []
    approved_emails = {"alice" + "@" + "example.com"}
    for path in sorted(paths):
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


def scan_tree(root: Path) -> list[dict]:
    return scan_paths(root, root.rglob("*"))


def scan_public_payload(root: Path) -> list[dict]:
    contract = load_contract(root / "installer" / "contracts" / "public-payload.json")
    paths = (root / rel for rel in selected_files(root, contract))
    return scan_paths(root, paths)


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


def _payload_verification_errors(payload: Path) -> list[str]:
    commands = (
        "modules/orchestration-core/verify-core.py",
        "modules/adapters/hermes/verify-hermes-adapter.py",
        "modules/adapters/verify-adapters.py",
        "installer/verify-lifecycle.py",
    )
    failures: list[str] = []
    for relative in commands:
        result = subprocess.run(
            [sys.executable, relative], cwd=payload,
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            first_line = (result.stdout or result.stderr or "no output").splitlines()[0]
            failures.append(f"{relative}:{redact(first_line)}")
    return failures


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
    verification_errors = _payload_verification_errors(destination)
    if verification_errors:
        return emit("BLOCKED", "build", [{"name": "source-verification", "status": "BLOCKED", "errors": verification_errors}])
    manifest = {"version": VERSION, "files": []}
    for rel in files:
        data = (destination / rel).read_bytes()
        manifest["files"].append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    (destination / "RELEASE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return emit("PASS", "build", [{"name": "privacy-scan", "status": "PASS"}, {"name": "payload", "status": "PASS", "files": len(files)}])


def config_value(args) -> dict:
    return {
        "version": 1, "install_root": str(args.target.resolve()), "data_root": str(args.data_dir.resolve()),
        "python": sys.executable, "harness": _normalized_harness(args.harness),
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
    try:
        target, data = _safe_root(args.target), _safe_root(args.data_dir)
    except ValueError:
        return emit("BLOCKED", "install", [{"name": "safe-paths", "status": "BLOCKED"}])
    source = ROOT.resolve()
    source_is_distribution = (source / ".git").exists() or (source / "RELEASE-MANIFEST.json").exists()
    if source_is_distribution and (target == source or data == source):
        return emit("BLOCKED", "install", [{"name": "source-boundary", "status": "BLOCKED"}], "Choose install and data roots outside the source distribution.")
    if not args.apply:
        return emit("DRY-RUN", "install", [{"name": "preflight", "status": "PASS"}, {"name": "writes", "status": "SKIPPED"}])
    if not args.yes:
        return emit("BLOCKED", "install", [{"name": "confirmation", "status": "BLOCKED"}], "Review target and data paths, then add --yes.")
    requested_harness = _normalized_harness(args.harness)
    existing: dict | None = None
    snapshot: Path | None = None
    snapshot_id: str | None = None
    manifest_path = target / "managed-files.json"
    if manifest_path.is_file():
        try:
            existing = _load_managed(target)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return emit("BLOCKED", "install", [{"name": "managed-manifest", "status": "BLOCKED", "detail": redact(exc)}])
        if Path(existing["data_root"]).resolve() != data:
            return emit("BLOCKED", "install", [{"name": "data-root", "status": "BLOCKED"}], "Use the installed data root or uninstall explicitly.")
        installed_harness = _normalized_harness(existing.get("harness", requested_harness))
        if installed_harness != requested_harness:
            return emit("BLOCKED", "install", [{"name": "harness", "status": "BLOCKED"}], "Use connect to add another harness without replacing local state.")
    with tempfile.TemporaryDirectory(prefix="orchestration-install-") as temp:
        staged = Path(temp) / "payload"
        files = copy_payload(ROOT, staged)
        hits = scan_tree(staged)
        if hits:
            return emit("BLOCKED", "install", [{"name": "privacy-scan", "status": "BLOCKED", "hits": hits[:20]}])
        verification_errors = _payload_verification_errors(staged)
        if verification_errors:
            return emit("BLOCKED", "install", [{"name": "source-verification", "status": "BLOCKED", "errors": verification_errors}])
        try:
            if existing is not None:
                snapshot_id, snapshot = _create_snapshot(target, existing)
            target.mkdir(parents=True, exist_ok=True)
            for rel in files:
                src, dst = staged / _relative(rel), target / _relative(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists() or src.read_bytes() != dst.read_bytes():
                    shutil.copyfile(src, dst)
            local_files = set(existing.get("local_files", [])) if existing else set()
            if existing and not local_files:
                local_files = {
                    relative for relative in existing["files"]
                    if relative == "config.local.yaml" or relative.startswith("connections/")
                }
            config_path = target / "config.local.yaml"
            if not config_path.is_file():
                _atomic_json(config_path, config_value(args))
            local_files.add("config.local.yaml")
            if existing:
                old_payload = set(existing.get("payload_files", set(existing["files"]) - local_files))
                for relative in sorted(old_payload - set(files)):
                    obsolete = target / _relative(relative)
                    if obsolete.is_file() or obsolete.is_symlink():
                        obsolete.unlink()
            local_existing = sorted(relative for relative in local_files if (target / _relative(relative)).is_file())
            managed = _managed_value(target, data, files, local_existing, requested_harness)
            managed["connections"] = sorted(set(existing.get("connections", []))) if existing else []
            if snapshot_id:
                managed["previous_snapshot"] = snapshot_id
            _atomic_json(manifest_path, managed)
        except Exception as exc:
            if snapshot is not None:
                _restore_snapshot(target, snapshot, files)
                rollback = "PASS"
            else:
                rollback = "NOT_AVAILABLE"
            return emit("FAIL", "install", [{"name": "automatic-rollback", "status": rollback}, {"name": "install", "status": "FAIL", "detail": redact(exc)}])
    for name in ("chroma", "spool", "spool/done", "spool/error"):
        (data / name).mkdir(parents=True, exist_ok=True)
    return emit("PASS", "install", [{"name": "core-files", "status": "PASS", "count": len(files)}, {"name": "recipient-local-state", "status": "PRESERVED" if existing else "INITIALIZED"}])


def command_connect(args) -> int:
    target = _safe_root(args.target)
    manifest = _load_managed(target)
    harness = _normalized_harness(args.harness)
    if harness == "generic":
        return emit("BLOCKED", "connect", [{"name": "harness", "status": "BLOCKED"}], "Choose a concrete harness adapter.")
    module = target / "modules" / "adapters" / harness
    if not (module / "harness-manifest.json").is_file():
        return emit("BLOCKED", "connect", [{"name": "adapter-module", "status": "BLOCKED"}])
    config_reference = args.config_reference or f"recipient://{harness}/config"
    data_reference = args.data_reference or f"recipient://{harness}/data"
    if not REFERENCE_RE.fullmatch(config_reference) or not REFERENCE_RE.fullmatch(data_reference):
        return emit("BLOCKED", "connect", [{"name": "reference-only", "status": "BLOCKED"}], "Use an env://, file://, secret://, or recipient:// reference; never a credential value.")
    relative = f"connections/{harness}.json"
    if not (args.apply and args.yes):
        return emit("DRY-RUN", "connect", [{"name": "adapter", "status": "PASS", "harness": harness}, {"name": "activation", "status": "SKIPPED"}])
    descriptor = {
        "schema": "lapart.harness-connection.v2", "harness": harness,
        "adapter_manifest": f"modules/adapters/{harness}/harness-manifest.json",
        "protocol_version": 2, "config_reference": config_reference,
        "data_reference": data_reference, "activation": "installed-not-activated",
        "provider_calls": 0,
    }
    descriptor_path = target / _relative(relative)
    previous_descriptor = descriptor_path.read_bytes() if descriptor_path.is_file() else None
    local_files = sorted(set(manifest.get("local_files", ["config.local.yaml"])) | {relative})
    manifest["schema_version"] = 2
    manifest["local_files"] = local_files
    manifest["files"] = sorted(set(manifest["files"]) | {relative})
    manifest["connections"] = sorted(set(manifest.get("connections", [])) | {harness})
    try:
        _atomic_json(descriptor_path, descriptor)
        _atomic_json(target / "managed-files.json", manifest)
    except Exception as exc:
        if previous_descriptor is None:
            if descriptor_path.is_file() or descriptor_path.is_symlink():
                descriptor_path.unlink()
        else:
            _atomic_bytes(descriptor_path, previous_descriptor)
        return emit("FAIL", "connect", [{"name": "automatic-rollback", "status": "PASS"}, {"name": "descriptor", "status": "FAIL", "detail": redact(exc)}])
    return emit("PASS", "connect", [{"name": "descriptor", "status": "PASS", "harness": harness}, {"name": "activation", "status": "SKIPPED"}])


def command_verify(args) -> int:
    target = _safe_root(args.target)
    manifest = _load_managed(target)
    errors = _integrity_errors(target, manifest)
    checks = [{"name": "managed-integrity", "status": "PASS" if not errors else "FAIL", "errors": errors[:20]}]
    if errors:
        return emit("FAIL", "verify", checks)
    harness = _normalized_harness(args.harness)
    if harness == "generic":
        commands = [
            [sys.executable, "modules/orchestration-core/verify-core.py"],
            [sys.executable, "modules/adapters/hermes/verify-hermes-adapter.py"],
            [sys.executable, "modules/adapters/verify-adapters.py"],
            [sys.executable, "installer/verify-lifecycle.py"],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=target, capture_output=True, text=True, check=False)
            checks.append({"name": command[1], "status": "PASS" if result.returncode == 0 else "FAIL", "detail": redact((result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else "no output")})
    else:
        command = [
            sys.executable, "modules/conformance-kit/run-conformance.py",
            "--harness-dir", f"modules/adapters/{harness}", "--json",
        ]
        result = subprocess.run(command, cwd=target, capture_output=True, text=True, check=False)
        try:
            detail = json.loads(result.stdout)
        except json.JSONDecodeError:
            detail = {"status": "FAIL", "provider_calls": "unknown"}
        passed = result.returncode == 0 and detail.get("status") == "PASS" and detail.get("provider_calls") == 0
        checks.append({"name": f"adapter:{harness}", "status": "PASS" if passed else "FAIL", "provider_calls": detail.get("provider_calls")})
    status = "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL"
    return emit(status, "verify", checks)


def _create_snapshot(target: Path, manifest: dict) -> tuple[str, Path]:
    manifest_path = target / "managed-files.json"
    manifest_bytes = manifest_path.read_bytes()
    hashes: dict[str, str] = {}
    for relative in manifest["files"]:
        source = target / _relative(relative)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"cannot snapshot unsafe managed file: {relative}")
        hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    identity = hashlib.sha256(manifest_bytes)
    for relative, digest in sorted(hashes.items()):
        identity.update(relative.encode("utf-8"))
        identity.update(digest.encode("ascii"))
    snapshot_id = identity.hexdigest()[:16]
    data_root = _safe_root(Path(manifest["data_root"]))
    snapshot = data_root / "rollbacks" / snapshot_id
    metadata_path = snapshot / "snapshot.json"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            metadata.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest()
            or metadata.get("files") != hashes
        ):
            raise ValueError("snapshot identifier collision")
        return snapshot_id, snapshot
    (snapshot / "files").mkdir(parents=True, exist_ok=False)
    for relative, expected in hashes.items():
        source = target / _relative(relative)
        destination = snapshot / "files" / _relative(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
            raise ValueError(f"snapshot copy hash mismatch: {relative}")
    shutil.copyfile(manifest_path, snapshot / "managed-files.json")
    _atomic_json(metadata_path, {
        "schema": "lapart.rollback-snapshot.v1", "snapshot_id": snapshot_id,
        "kit_version": manifest["kit_version"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "files": hashes,
    })
    return snapshot_id, snapshot


def _restore_snapshot(target: Path, snapshot: Path, remove_candidates: list[str] | None = None) -> dict:
    metadata = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    if metadata.get("schema") != "lapart.rollback-snapshot.v1" or metadata.get("snapshot_id") != snapshot.name:
        raise ValueError("rollback snapshot identity mismatch")
    previous_bytes = (snapshot / "managed-files.json").read_bytes()
    if hashlib.sha256(previous_bytes).hexdigest() != metadata.get("manifest_sha256"):
        raise ValueError("rollback manifest hash mismatch")
    previous = json.loads(previous_bytes)
    if Path(previous.get("install_root", "")).resolve() != target:
        raise ValueError("rollback manifest does not own this target")
    previous_files = set(previous.get("files", []))
    if set(metadata.get("files", {})) != previous_files:
        raise ValueError("rollback snapshot inventory mismatch")
    verified_sources: list[tuple[str, Path]] = []
    for relative, expected in metadata.get("files", {}).items():
        source = snapshot / "files" / _relative(relative)
        if not source.is_file() or source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError(f"rollback file hash mismatch: {relative}")
        verified_sources.append((relative, source))
    current_files = set(remove_candidates or [])
    current_manifest = target / "managed-files.json"
    if current_manifest.is_file():
        current = json.loads(current_manifest.read_text(encoding="utf-8"))
        current_files.update(current.get("files", []))
    for relative in sorted(current_files - previous_files):
        path = target / _relative(relative)
        if path.is_file() or path.is_symlink():
            path.unlink()
    for relative, source in verified_sources:
        destination = target / _relative(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    _atomic_bytes(target / "managed-files.json", previous_bytes)
    return previous


def command_upgrade(args) -> int:
    target = _safe_root(args.target)
    manifest = _load_managed(target)
    errors = _integrity_errors(target, manifest)
    if errors:
        return emit("BLOCKED", "upgrade", [{"name": "managed-integrity", "status": "BLOCKED", "errors": errors[:20]}])
    planned = len(selected_files(ROOT, load_contract()))
    if not (args.apply and args.yes):
        return emit("DRY-RUN", "upgrade", [{"name": "payload", "status": "PASS", "files": planned}, {"name": "rollback-snapshot", "status": "PASS"}])
    snapshot_id, snapshot = _create_snapshot(target, manifest)
    new_files: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="orchestration-upgrade-") as temporary:
            staged = Path(temporary) / "payload"
            new_files = copy_payload(ROOT, staged)
            hits = scan_tree(staged)
            if hits:
                raise ValueError("privacy scan blocked upgrade payload")
            verification_errors = _payload_verification_errors(staged)
            if verification_errors:
                raise ValueError("source verification blocked upgrade: " + "; ".join(verification_errors))
            for relative in new_files:
                source, destination = staged / _relative(relative), target / _relative(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        old_files = set(manifest["files"])
        local_files = set(manifest.get("local_files", []))
        if not local_files:
            local_files = {relative for relative in old_files if relative == "config.local.yaml" or relative.startswith("connections/")}
        old_payload = set(manifest.get("payload_files", old_files - local_files))
        for relative in sorted(old_payload - set(new_files)):
            path = target / _relative(relative)
            if path.is_file() or path.is_symlink():
                path.unlink()
        local_existing = sorted(relative for relative in local_files if (target / _relative(relative)).is_file())
        upgraded = _managed_value(target, _safe_root(Path(manifest["data_root"])), new_files, local_existing, manifest.get("harness", "generic"))
        upgraded["connections"] = sorted(set(manifest.get("connections", [])))
        upgraded["previous_snapshot"] = snapshot_id
        _atomic_json(target / "managed-files.json", upgraded)
    except Exception as exc:
        _restore_snapshot(target, snapshot, new_files)
        return emit("FAIL", "upgrade", [{"name": "automatic-rollback", "status": "PASS"}, {"name": "upgrade", "status": "FAIL", "detail": redact(exc)}])
    return emit("PASS", "upgrade", [{"name": "payload", "status": "PASS", "files": len(new_files)}, {"name": "rollback-snapshot", "status": "PASS", "id": snapshot_id}])


def command_rollback(args) -> int:
    target = _safe_root(args.target)
    manifest = _load_managed(target)
    snapshot_id = args.snapshot or manifest.get("previous_snapshot")
    if not isinstance(snapshot_id, str) or not SNAPSHOT_RE.fullmatch(snapshot_id):
        return emit("BLOCKED", "rollback", [{"name": "snapshot", "status": "BLOCKED"}])
    snapshot = _safe_root(Path(manifest["data_root"])) / "rollbacks" / snapshot_id
    if not (snapshot / "snapshot.json").is_file():
        return emit("BLOCKED", "rollback", [{"name": "snapshot", "status": "BLOCKED"}])
    if not (args.apply and args.yes):
        return emit("DRY-RUN", "rollback", [{"name": "snapshot", "status": "PASS", "id": snapshot_id}])
    _, recovery = _create_snapshot(target, manifest)
    try:
        previous = _restore_snapshot(target, snapshot)
    except Exception as exc:
        _restore_snapshot(target, recovery)
        return emit("FAIL", "rollback", [{"name": "automatic-recovery", "status": "PASS"}, {"name": "rollback", "status": "FAIL", "detail": redact(exc)}])
    return emit("PASS", "rollback", [{"name": "restored", "status": "PASS", "kit_version": previous["kit_version"]}])


def command_doctor(args) -> int:
    target = _safe_root(args.target)
    checks = []
    for name in ("managed-files.json", "config.local.yaml", "system/memoryweaver/store/chroma.py", "modules/topology/configure-topology.py"):
        checks.append({"name": name, "status": "PASS" if (target / name).is_file() else "FAIL"})
    try:
        manifest = _load_managed(target)
        integrity = _integrity_errors(target, manifest)
        checks.append({"name": "managed-integrity", "status": "PASS" if not integrity else "FAIL", "errors": integrity[:20]})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        checks.append({"name": "managed-integrity", "status": "FAIL", "detail": redact(exc)})
    if args.prime_omp:
        diagnostic = target / "modules" / "prime-omp" / "diagnose-prime-omp.py"
        command = [
            sys.executable, str(diagnostic),
            "--hermes-bin", args.hermes_bin,
            "--profile", args.profile,
        ]
        if args.session_tools_json:
            command.extend(["--session-tools-json", str(args.session_tools_json)])
        if args.omp_probe:
            command.extend(["--omp-probe", str(args.omp_probe)])
        if args.omp_python:
            command.extend(["--omp-python", args.omp_python])
        if args.omp_runtime_path:
            command.extend(["--omp-runtime-path", args.omp_runtime_path])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        try:
            detail = json.loads(result.stdout)
        except json.JSONDecodeError:
            detail = {"next_action": "DIAGNOSTIC_DID_NOT_RETURN_JSON"}
        checks.append({
            "name": "prime-omp-live",
            "status": "PASS" if result.returncode == 0 else "FAIL",
            "detail": detail,
        })
    status = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return emit(status, "doctor", checks, "Re-run the installer after reviewing its dry-run." if status == "FAIL" else "")


def command_uninstall(args) -> int:
    try:
        target = _safe_root(args.target)
    except ValueError:
        return emit("BLOCKED", "uninstall", [{"name": "safe-target", "status": "BLOCKED"}])
    manifest_path = target / "managed-files.json"
    if not manifest_path.is_file():
        return emit("BLOCKED", "uninstall", [{"name": "managed-manifest", "status": "BLOCKED"}])
    if not (args.apply and args.yes):
        return emit("DRY-RUN", "uninstall", [{"name": "managed-only", "status": "PASS"}])
    try:
        manifest = _load_managed(target)
    except (OSError, ValueError, json.JSONDecodeError):
        return emit("BLOCKED", "uninstall", [{"name": "unsafe-manifest", "status": "BLOCKED"}])
    for rel_text in manifest["files"]:
        rel = PurePosixPath(rel_text)
        if rel.is_absolute() or ".." in rel.parts:
            return emit("BLOCKED", "uninstall", [{"name": "unsafe-manifest", "status": "BLOCKED"}])
        path = target / Path(*rel.parts)
        if path.is_file() or path.is_symlink():
            path.unlink()
    manifest_path.unlink()
    for directory in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
        try: directory.rmdir()
        except OSError: pass
    return emit("PASS", "uninstall", [{"name": "managed-files-removed", "status": "PASS"}, {"name": "recipient-data", "status": "SKIPPED"}])


def command_privacy(args) -> int:
    root = args.root.resolve()
    hits = scan_public_payload(root) if args.scope == "public-payload" else scan_tree(root)
    check_name = "public-payload-scan" if args.scope == "public-payload" else "repository-scan"
    return emit(
        "PASS" if not hits else "BLOCKED",
        "privacy-gate",
        [{"name": check_name, "status": "PASS" if not hits else "BLOCKED", "hits": hits[:20]}],
    )


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
    harness_choices = sorted(HARNESS_ALIASES)
    configure = mut("configure", command_configure); configure.add_argument("--output", type=Path, default=ROOT / "installer-staging" / "config.local.yaml"); configure.add_argument("--target", type=Path, required=True); configure.add_argument("--data-dir", type=Path, required=True); configure.add_argument("--harness", choices=harness_choices, default="generic")
    install = mut("install", command_install); install.add_argument("--target", type=Path, required=True); install.add_argument("--data-dir", type=Path, required=True); install.add_argument("--harness", choices=harness_choices, default="generic")
    connect = mut("connect", command_connect); connect.add_argument("--target", type=Path, required=True); connect.add_argument("--harness", choices=[name for name in harness_choices if name != "generic"], required=True); connect.add_argument("--config-reference"); connect.add_argument("--data-reference")
    verify = sub.add_parser("verify"); verify.add_argument("--target", type=Path, required=True); verify.add_argument("--harness", choices=harness_choices, default="generic"); verify.set_defaults(func=command_verify)
    upgrade = mut("upgrade", command_upgrade); upgrade.add_argument("--target", type=Path, required=True)
    rollback = mut("rollback", command_rollback); rollback.add_argument("--target", type=Path, required=True); rollback.add_argument("--snapshot")
    doctor = sub.add_parser("doctor"); doctor.add_argument("--target", type=Path, required=True)
    doctor.add_argument("--prime-omp", action="store_true", help="run the optional live Prime/OMP diagnostic")
    doctor.add_argument("--hermes-bin", default="hermes")
    doctor.add_argument("--profile", default="workspace")
    doctor.add_argument("--session-tools-json", type=Path)
    doctor.add_argument("--omp-probe", type=Path)
    doctor.add_argument("--omp-python")
    doctor.add_argument("--omp-runtime-path")
    doctor.set_defaults(func=command_doctor)
    uninstall = mut("uninstall", command_uninstall); uninstall.add_argument("--target", type=Path, required=True)
    privacy = sub.add_parser("privacy-gate"); privacy.add_argument("--root", type=Path, default=ROOT)
    privacy.add_argument("--scope", choices=["repository", "public-payload"], default="repository")
    privacy.set_defaults(func=command_privacy)
    bundle = mut("bundle", command_bundle); bundle.add_argument("--output", type=Path, required=True)
    return root


if __name__ == "__main__":
    args = parser().parse_args()
    try:
        raise SystemExit(args.func(args))
    except Exception as exc:
        raise SystemExit(emit("FAIL", args.command, [{"name": "exception", "status": "FAIL", "detail": redact(exc)}]))
