#!/usr/bin/env python3
"""Stage, uninstall, or roll back adaptive v2 without changing activation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


MODULE = Path(__file__).resolve().parent
MANAGED_FILES = (
    "README.md",
    "additive-parallel.profile.json",
    "parallel-profile.schema.json",
    "verify-additive-parallel.py",
    "adaptive-v2.adapter.example.json",
    "adaptive-v2.adapter.schema.json",
    "adaptive-v2.catalog.json",
    "adaptive-v2.policy.json",
    "adaptive-v2.review.json",
    "adaptive-v2-policy.schema.json",
    "adaptive-switch.py",
    "adaptive_switch.py",
    "orca-observer.py",
    "orca_observer_switch.py",
    "orchestration-control.py",
    "orchestration_switch.py",
    "adaptive_v2.py",
    "career-shadow.schema.json",
    "source-projection.json",
    "source-hashes.json",
    "prime_bridge/harness_registry.py",
    "prime_bridge/git_interop.py",
    "prime_bridge/adaptive_execution_provider.py",
    "prime_bridge/orca_adaptive_batch.py",
    "prime_bridge/orca_control.py",
    "prime_bridge/orca_execution.py",
    "prime_bridge/orca_observer_switch.py",
    "prime_bridge/orca_operator.py",
    "prime_bridge/parallel_provider.py",
    "prime_bridge/parallel_provider_manager.py",
    "scripts/manage-orca.py",
    "scripts/qualify-orca-coding.py",
    "scripts/manage-parallel-provider.py",
    "scripts/orca-observer.py",
    "scripts/orca-spawn.mjs",
    "config/orca-harness-adapters.json",
    "config/orca-provider.manifest.json",
    "config/parallel-worker-providers.json",
    "docs/ORCA-EXECUTION-PROVIDER.md",
    "verify-adaptive-v2.py",
)


def _safe(path: Path) -> Path:
    result = path.resolve()
    if result == Path(result.anchor) or result == MODULE.resolve():
        raise ValueError("target must be a dedicated recipient directory")
    return result


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_id(target: Path) -> str:
    state = []
    for relative in MANAGED_FILES:
        path = target / relative
        state.append((relative, _digest(path) if path.is_file() else None))
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()[:16]


def stage(target: Path, backup_root: Path, *, apply: bool, yes: bool) -> dict:
    target, backup_root = _safe(target), _safe(backup_root)
    if not apply:
        return {"status": "DRY-RUN", "operation": "stage", "activation": "unchanged", "files": list(MANAGED_FILES)}
    if not yes:
        return {"status": "BLOCKED", "operation": "stage", "reason": "CONFIRMATION_REQUIRED"}
    snapshot = _snapshot_id(target)
    snapshot_root = backup_root / snapshot
    if snapshot_root.exists():
        raise ValueError("snapshot identity already exists")
    (snapshot_root / "files").mkdir(parents=True)
    prior = {}
    for relative in MANAGED_FILES:
        destination = target / PurePosixPath(relative)
        prior[relative] = destination.is_file()
        if destination.is_file():
            backup = snapshot_root / "files" / PurePosixPath(relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(destination, backup)
    (snapshot_root / "snapshot.json").write_text(json.dumps({"schema": "lapart.adaptive-v2-snapshot.v1", "snapshot": snapshot, "prior": prior}, indent=2) + "\n", encoding="utf-8")
    target.mkdir(parents=True, exist_ok=True)
    for relative in MANAGED_FILES:
        source, destination = MODULE / relative, target / PurePosixPath(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return {"status": "PASS", "operation": "stage", "snapshot": snapshot, "activation": "unchanged", "feature_state": "inactive"}


def rollback(target: Path, backup_root: Path, snapshot: str, *, apply: bool, yes: bool) -> dict:
    target, backup_root = _safe(target), _safe(backup_root)
    if len(snapshot) != 16 or any(char not in "0123456789abcdef" for char in snapshot):
        return {"status": "BLOCKED", "operation": "rollback", "reason": "SNAPSHOT_INVALID"}
    snapshot_root = backup_root / snapshot
    descriptor = snapshot_root / "snapshot.json"
    if not descriptor.is_file():
        return {"status": "BLOCKED", "operation": "rollback", "reason": "SNAPSHOT_MISSING"}
    value = json.loads(descriptor.read_text(encoding="utf-8"))
    if value.get("schema") != "lapart.adaptive-v2-snapshot.v1" or value.get("snapshot") != snapshot or set(value.get("prior", {})) != set(MANAGED_FILES):
        return {"status": "BLOCKED", "operation": "rollback", "reason": "SNAPSHOT_INVALID"}
    if not apply:
        return {"status": "DRY-RUN", "operation": "rollback", "snapshot": snapshot}
    if not yes:
        return {"status": "BLOCKED", "operation": "rollback", "reason": "CONFIRMATION_REQUIRED"}
    for relative, existed in value["prior"].items():
        destination = target / PurePosixPath(relative)
        backup = snapshot_root / "files" / PurePosixPath(relative)
        if existed:
            if not backup.is_file():
                return {"status": "BLOCKED", "operation": "rollback", "reason": "SNAPSHOT_INCOMPLETE"}
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(backup, destination)
        elif destination.is_file():
            destination.unlink()
    return {"status": "PASS", "operation": "rollback", "snapshot": snapshot, "activation": "unchanged"}


def uninstall(target: Path, backup_root: Path, *, apply: bool, yes: bool) -> dict:
    target, backup_root = _safe(target), _safe(backup_root)
    if not apply:
        return {"status": "DRY-RUN", "operation": "uninstall", "activation": "unchanged", "files": list(MANAGED_FILES)}
    if not yes:
        return {"status": "BLOCKED", "operation": "uninstall", "reason": "CONFIRMATION_REQUIRED"}
    snapshot = _snapshot_id(target)
    snapshot_root = backup_root / snapshot
    if snapshot_root.exists():
        raise ValueError("snapshot identity already exists")
    (snapshot_root / "files").mkdir(parents=True)
    prior = {}
    for relative in MANAGED_FILES:
        source = target / PurePosixPath(relative)
        prior[relative] = source.is_file()
        if source.is_file():
            backup = snapshot_root / "files" / PurePosixPath(relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, backup)
    (snapshot_root / "snapshot.json").write_text(
        json.dumps({"schema": "lapart.adaptive-v2-snapshot.v1", "snapshot": snapshot, "prior": prior}, indent=2) + "\n",
        encoding="utf-8",
    )
    for relative in MANAGED_FILES:
        destination = target / PurePosixPath(relative)
        if destination.is_file():
            destination.unlink()
    return {"status": "PASS", "operation": "uninstall", "snapshot": snapshot, "activation": "unchanged"}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("stage", "rollback", "uninstall"):
        command = sub.add_parser(name)
        command.add_argument("--target", type=Path, required=True)
        command.add_argument("--backup-root", type=Path, required=True)
        command.add_argument("--apply", action="store_true")
        command.add_argument("--yes", action="store_true")
        if name == "rollback":
            command.add_argument("--snapshot", required=True)
    args = parser.parse_args()
    try:
        if args.command == "stage":
            result = stage(args.target, args.backup_root, apply=args.apply, yes=args.yes)
        elif args.command == "rollback":
            result = rollback(args.target, args.backup_root, args.snapshot, apply=args.apply, yes=args.yes)
        else:
            result = uninstall(args.target, args.backup_root, apply=args.apply, yes=args.yes)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "FAIL", "operation": args.command, "reason": type(exc).__name__}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"PASS", "DRY-RUN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
