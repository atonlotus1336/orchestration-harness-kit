#!/usr/bin/env python3
"""Verify that the private snapshot packages Herdr code but no local runtime."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC_PATH = ROOT / "tools" / "sync-private-system-snapshot.py"
SPEC = importlib.util.spec_from_file_location("snapshot_sync", SYNC_PATH)
SYNC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SYNC)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    failures: list[str] = []
    for relative in sorted(SYNC.HERDR_EVALUATOR_PATHS):
        if not SYNC.hermes_selected(Path(relative)):
            failures.append(f"required path rejected: {relative}")

    denied = (
        Path(".state/herdr-review/runtime-lock.json"),
        Path("evidence/herdr-evaluator/supervised-soak.json"),
        Path("runtime/herdr.exe"),
    )
    for relative in denied:
        if SYNC.hermes_selected(relative):
            failures.append(f"local/runtime path admitted: {relative.as_posix()}")

    manifest_path = ROOT / "system-snapshot/SNAPSHOT-MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {item["path"]: item for item in manifest["files"]}
    prefix = "hermes-agent-self-evolution/"
    required = set(SYNC.HERDR_EVALUATOR_PATHS) | {
        "prime_bridge/adaptive_review/herdr_client.py",
        "prime_bridge/adaptive_review/herdr_isolation.py",
        "prime_bridge/adaptive_review/herdr_transport.py",
    }
    for relative in sorted(required):
        key = prefix + relative
        item = entries.get(key)
        path = ROOT / "system-snapshot" / key
        if item is None or not path.is_file():
            failures.append(f"snapshot path missing: {relative}")
        elif sha256(path) != item.get("sha256"):
            failures.append(f"manifest hash mismatch: {relative}")

    skill_dependencies = set(SYNC.ADAPTIVE_REVIEW_SKILL_DEPENDENCIES)
    for relative in sorted(skill_dependencies):
        key = "skills-shared/" + relative
        item = entries.get(key)
        path = ROOT / "system-snapshot" / key
        if not SYNC.skill_dependency_selected(Path(relative)):
            failures.append(f"reviewer dependency rejected: {relative}")
        elif item is None or not path.is_file():
            failures.append(f"reviewer dependency missing: {relative}")
        elif sha256(path) != item.get("sha256"):
            failures.append(f"reviewer dependency hash mismatch: {relative}")

    result = {
        "status": "PASS" if not failures else "FAIL",
        "required_paths": len(required),
        "denied_runtime_paths": len(denied),
        "reviewer_dependencies": len(skill_dependencies),
        "failures": failures,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
