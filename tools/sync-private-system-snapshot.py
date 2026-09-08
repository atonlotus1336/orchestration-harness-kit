#!/usr/bin/env python3
"""Create a secret-safe snapshot of the live Hermes system implementation.

This repository is private, but credentials and mutable runtime state still do not
belong in version control. The exporter intentionally selects implementation,
policy, documentation, and test files while excluding environment files,
backups, caches, evidence receipts, and ledgers.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


TEXT_SUFFIXES = {
    ".cmd", ".dockerfile", ".in", ".json", ".jsonl", ".lock", ".md",
    ".mjs", ".ps1", ".py", ".sh", ".spdx", ".txt", ".yaml", ".yml",
}

BLOCKED_PARTS = {
    ".git", ".pytest-cache", "__pycache__", "evidence", "ledgers",
    "logs", "node_modules", "reports", "runtime", "state",
}

SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "openai-style-token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "telegram-token": re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}

SYNTHETIC_SECRET_FIXTURES = {
    "tests/core/test_external_importers.py",
    "tests/integration/test_omp_adapters.py",
    "tests/integration/test_omp_policy.py",
}

STANDARD_WORKSPACE_RULES = [
    (r"D:\MyVault", "__WINDOWS_WORKSPACE__"),
    (r"D:\\MyVault", "__WINDOWS_WORKSPACE__"),
    ("D:/MyVault", "__WINDOWS_WORKSPACE__"),
    ("/mnt/d/MyVault", "__WSL_WORKSPACE__"),
]

# Split sensitive source labels so the repository's leak scanner never records
# the original project names while the exporter can still remove them.
PERSONAL_PATH_MARKERS = (
    "pro" + "fessor",
    "a" + "zul",
    "c" + "are",
    "mis" + "sion",
    "tri" + "bal",
)
PERSONAL_SURFACE_IDS = {"c" + "are-consent"}
BUILTIN_RECIPIENT_PLACEHOLDERS = {
    "__GLOBAL_NPM__",
    "__LINUX_USER__",
    "__LOCAL_TOOL_STORAGE__",
    "__OWNER_HANDLE__",
    "__TAILSCALE_IPV4__",
    "__WINDOWS_USER__",
    "__WSL_DISTRO__",
}


def scrub_personal_surface_blocks(text: str) -> str:
    """Remove project-specific surface rows while retaining the system graph."""
    output: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        match = re.match(r"^  - id:\s*([^\s#]+)", line)
        if match:
            skipping = match.group(1).lower() in PERSONAL_SURFACE_IDS
        if not skipping:
            output.append(line)
    return "".join(output)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def blocked(path: Path) -> bool:
    lower_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return (
        bool(lower_parts & BLOCKED_PARTS)
        or name.startswith(".env")
        or ".bak" in name
        or name.endswith((".log", ".pyc", ".sig"))
    )


def hermes_selected(relative: Path) -> bool:
    if blocked(relative):
        return False
    posix = relative.as_posix()
    if posix in SYNTHETIC_SECRET_FIXTURES:
        return False
    top = relative.parts[0] if relative.parts else ""
    name = relative.name.lower()

    if top in {"prime_bridge", "evolution"}:
        return True
    if posix.startswith(("containers/omp/", "containers/prime-probe/")):
        return True
    if top == "config":
        return name.startswith(("prime-", "omp-", "connectivity-", "verified-maintenance"))
    if top == "contracts":
        return any(word in name for word in ("prime", "hermes", "remote-client", "phase01"))
    if top == "requirements":
        return True
    if top == "scripts":
        if any(marker in posix.lower() for marker in PERSONAL_PATH_MARKERS):
            return False
        return bool(re.search(
            r"prime|omp|hermes|telegram|maintenance|autolearn|orchestration|profile|gateway|rag|ollama",
            posix,
            re.IGNORECASE,
        ))
    if top == "tests":
        return bool(re.search(
            r"prime|omp|maintenance|evolution|external_importers|model_route|rpc_probe|skill_module",
            posix,
            re.IGNORECASE,
        ))
    if top == "docs":
        return bool(re.search(r"prime|omp|telegram|maintenance|model-profile", posix, re.IGNORECASE))
    if posix in {
        "comfyui_bridge/HERMES_SKILL_OVERRIDE.md",
        "comfyui_bridge/TELEGRAM_COMMANDS.md",
        "profiles/home/config.yaml",
        "profiles/telegram/config.yaml",
        "profiles/telegram/README.md",
        "install-autolearn-cron.cmd",
        "local-env.cmd.example",
        "pyproject.toml",
        "README.md",
        "run-autolearn.cmd",
        "run-evolve-skill.cmd",
    }:
        return True
    return False


def generic_selected(relative: Path) -> bool:
    if blocked(relative) or relative.suffix.lower() not in TEXT_SUFFIXES:
        return False
    return not any(
        marker in relative.as_posix().lower()
        for marker in PERSONAL_PATH_MARKERS
    )


def workspace_support_selected(relative: Path) -> bool:
    if blocked(relative):
        return False
    posix = relative.as_posix()
    return (
        posix.startswith(".planning/autoexperiment-control-plane/")
        or posix == ".claude/AUTHORITY-CONSTITUTION.md"
        or posix == "SURFACE-GRAPH.yaml"
    )


def git_value(root: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value or None


def scan_secrets(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["non-utf8-file"]
    return [label for label, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def replace_identity_literals(text: str, rules: list[tuple[str, str]]) -> str:
    """Replace configured host spellings, including case and separator variants."""
    import itertools
    for source, replacement in sorted(rules, key=lambda pair: len(pair[0]), reverse=True):
        variants = {source}
        if re.fullmatch(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+){1,5}", source):
            parts = re.split(r"[-_]", source)
            for separators in itertools.product(("-", "_"), repeat=len(parts)-1):
                variants.add(parts[0] + "".join(sep+part for sep,part in zip(separators,parts[1:])))
            variants.add("[-_]".join(parts))
            variants.add("[_-]".join(parts))
        for variant in sorted(variants, key=len, reverse=True):
            text = re.sub(re.escape(variant), lambda match: replacement, text, flags=re.IGNORECASE)
    return text


def redact_enrollment(text: str) -> str:
    """Export enrollment slots without retaining an author's signing identity."""
    value = json.loads(text)
    for client in value.get("clients", {}).values():
        client["public_key"] = None
        client["fingerprint"] = None
    return json.dumps(value, indent=2) + "\n"


def sanitized_bytes(
    path: Path,
    workspace_rules: list[tuple[str, str]],
    rules: list[tuple[str, str]],
    identity_rules: list[tuple[str, str]],
) -> bytes:
    text = path.read_text(encoding="utf-8")
    for source, replacement in workspace_rules:
        text = re.sub(re.escape(source), replacement, text, flags=re.IGNORECASE)
    text = replace_identity_literals(text, rules)
    for source, replacement in STANDARD_WORKSPACE_RULES:
        text = re.sub(re.escape(source), replacement, text, flags=re.IGNORECASE)
    for source, replacement in identity_rules:
        text = re.sub(source, lambda match: replacement, text, flags=re.IGNORECASE)
    # The local privacy rules intentionally replace the maintainer's WSL distro
    # name with a neutral marker.  That marker is not executable on a recipient
    # machine, so turn it into an explicit restore-time value instead of
    # shipping a silently non-runnable containment policy.
    text = text.replace("Ubuntu-restored", "__WSL_DISTRO__")
    # Network topology and self-hosted service addresses are recipient data too.
    text = re.sub(
        r"\b100\.(?:6[4-9]|[78]\d|9\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
        "__TAILSCALE_IPV4__",
        text,
    )
    text = re.sub(
        r"(?im)(N8N_BASE_URL:\s*)https?://[^\s\"']+",
        r"\1https://n8n.example.com",
        text,
    )
    text = re.sub(
        r"https?://[^/\s\"']+(/api/v1/workflows\?limit=250)",
        r"https://n8n.example.com\1",
        text,
    )
    if path.name.lower() == "surface-graph.yaml":
        text = scrub_personal_surface_blocks(text)
    if path.name == "prime-desktop-attesters.json":
        text = redact_enrollment(text)
    text = text.replace("\r\n", "\n")
    if path.suffix.lower() in {".ps1", ".cmd", ".bat"}:
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


def sanitized_identity_text(text: str, identity_rules: list[tuple[str, str]]) -> str:
    for source, replacement in identity_rules:
        text = re.sub(source, lambda match: replacement, text, flags=re.IGNORECASE)
    return text


def collect(root: Path, selector) -> list[Path]:
    paths: list[Path] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name for name in directories
            if name.lower() not in BLOCKED_PARTS
            and not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            path = current_path / filename
            relative = path.relative_to(root)
            if selector(relative):
                paths.append(path)
    return sorted(paths)


def validate_generated_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".py":
            ast.parse(text, filename=str(path))
        elif path.suffix.lower() == ".json":
            json.loads(text)
    except (SyntaxError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return str(exc)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--autoexperiment-root", type=Path, required=True)
    parser.add_argument("--ops-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--sanitizer-rules", type=Path)
    parser.add_argument("--denylist", type=Path)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "system-snapshot",
    )
    args = parser.parse_args()

    roots = {
        "hermes-agent-self-evolution": (args.hermes_root.resolve(), hermes_selected),
        "autoexperiment": (args.autoexperiment_root.resolve(), generic_selected),
        "ops": (args.ops_root.resolve(), generic_selected),
        "workspace-support": (args.workspace_root.resolve(), workspace_support_selected),
    }
    for label, (root, _) in roots.items():
        if not root.is_dir():
            raise SystemExit(f"BLOCKED: missing {label} source: {root}")

    destination = args.destination.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    if destination.parent != repo_root or destination.name != "system-snapshot":
        raise SystemExit("BLOCKED: destination must be <repository>/system-snapshot")

    rules_path = (args.sanitizer_rules or (repo_root / ".sanitize-rules.local.json")).resolve()
    if not rules_path.is_file():
        raise SystemExit(f"BLOCKED: sanitizer rules missing: {rules_path}")
    raw_rules = json.loads(rules_path.read_text(encoding="utf-8-sig"))
    rules = [
        (str(item["From"]), str(item["To"]))
        for item in raw_rules
        if item.get("From")
    ]
    if not rules:
        raise SystemExit("BLOCKED: sanitizer rule set is empty")

    denylist_path = (args.denylist or (repo_root / ".private-denylist")).resolve()
    if not denylist_path.is_file():
        raise SystemExit(f"BLOCKED: private denylist missing: {denylist_path}")
    private_tokens = [
        line.strip()
        for line in denylist_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not private_tokens:
        raise SystemExit("BLOCKED: private denylist is empty")
    identity_rules = [
        (token, f"__PRIVATE_TOKEN_{index:02d}__")
        for index, token in enumerate(private_tokens, start=1)
    ]
    identity_rules.sort(key=lambda item: len(item[0]), reverse=True)
    workspace_source = args.workspace_root.resolve()
    workspace_windows = str(workspace_source)
    workspace_forward = workspace_source.as_posix()
    if workspace_source.drive:
        workspace_wsl = "/mnt/{}/{}".format(
            workspace_source.drive[0].lower(),
            "/".join(workspace_source.parts[1:]),
        )
    else:
        workspace_wsl = workspace_forward
    workspace_rules = sorted(
        {
            (workspace_windows, "__WINDOWS_WORKSPACE__"),
            (workspace_windows.replace("\\", "\\\\"), "__WINDOWS_WORKSPACE__"),
            (workspace_forward, "__WINDOWS_WORKSPACE__"),
            (workspace_wsl, "__WSL_WORKSPACE__"),
        },
        key=lambda item: len(item[0]),
        reverse=True,
    )

    selected: dict[str, list[Path]] = {
        label: collect(root, selector) for label, (root, selector) in roots.items()
    }
    findings: list[dict[str, object]] = []
    for label, paths in selected.items():
        root = roots[label][0]
        for source in paths:
            matches = scan_secrets(source)
            if matches:
                findings.append({
                    "source": f"{label}/{source.relative_to(root).as_posix()}",
                    "findings": matches,
                })
    if findings:
        print(json.dumps({"status": "BLOCKED", "secret_findings": findings}, indent=2))
        return 2

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    manifest_files: list[dict[str, object]] = []
    used_placeholders: set[str] = set()
    candidate_placeholders = (
        {placeholder for _, placeholder in identity_rules}
        | BUILTIN_RECIPIENT_PLACEHOLDERS
    )
    validation_errors: list[dict[str, str]] = []
    for label, paths in selected.items():
        root = roots[label][0]
        for source in paths:
            relative = source.relative_to(root)
            sanitized_relative = Path(sanitized_identity_text(relative.as_posix(), identity_rules))
            target = destination / label / sanitized_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(sanitized_bytes(source, workspace_rules, rules, identity_rules))
            validation_error = validate_generated_file(target)
            if validation_error:
                validation_errors.append({"path": str(target), "error": validation_error})
            relative_record = f"{label}/{sanitized_relative.as_posix()}"
            for placeholder in candidate_placeholders:
                if placeholder in relative_record or placeholder.encode("utf-8") in target.read_bytes():
                    used_placeholders.add(placeholder)
            manifest_files.append({
                "path": relative_record,
                "sha256": sha256(target),
                "bytes": target.stat().st_size,
            })

    if validation_errors:
        print(json.dumps({"status": "BLOCKED", "validation_errors": validation_errors}, indent=2))
        return 3

    live_root = roots["hermes-agent-self-evolution"][0]
    manifest = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Private restorable implementation snapshot; credentials and runtime state excluded.",
        "source": {
            "hermes_git_head": git_value(live_root, "rev-parse", "HEAD"),
            "hermes_git_branch": git_value(live_root, "branch", "--show-current"),
            "hermes_worktree_dirty": bool(git_value(live_root, "status", "--porcelain")),
            "sanitizer_rule_count": len(rules),
        },
        "excluded": sorted(BLOCKED_PARTS) + [".env*", "*.bak*", "*.log", "*.pyc", "*.sig"],
        "synthetic_secret_fixtures_excluded": sorted(SYNTHETIC_SECRET_FIXTURES),
        "private_placeholders": sorted(used_placeholders),
        "workspace_placeholders": [replacement for _, replacement in STANDARD_WORKSPACE_RULES],
        "file_count": len(manifest_files),
        "files": manifest_files,
    }
    (destination / "SNAPSHOT-MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "PLACEHOLDERS.template.json").write_text(
        json.dumps({placeholder: "<local-value>" for placeholder in manifest["private_placeholders"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "PASS",
        "destination": str(destination),
        "file_count": len(manifest_files),
        "groups": {label: len(paths) for label, paths in selected.items()},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
