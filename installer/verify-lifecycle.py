#!/usr/bin/env python3
"""Provider-free current-source verification for the portable SDK lifecycle."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from installer import kit


SOURCE_PATHS = {
    ".gitattributes",
    "README.md",
    "docs/QUICK-START.md",
    "docs/UPDATE-AND-REMOVE.md",
    "installer/README.md",
    "installer/contracts/harness-connection.schema.json",
    "installer/contracts/managed-files.schema.json",
    "installer/contracts/public-payload.json",
    "installer/contracts/rollback-snapshot.schema.json",
    "installer/kit.py",
    "installer/lifecycle-qualification.json",
    "installer/lifecycle-qualification-phase80.json",
    "installer/tests/test_release_core.py",
    "installer/tests/test_lifecycle_commands.py",
    "installer/verify-lifecycle.py",
    "modules/adapters/README.md",
    "modules/adapters/qualification-phase78.json",
    "modules/adapters/verify-adapters.py",
}
REQUIRED_COMMANDS = {
    "install", "connect", "doctor", "verify", "upgrade", "rollback", "uninstall",
}
REQUIRED_HARNESSES = {
    "hermes", "opencode", "pi", "antigravity", "codex", "claude-code", "moa",
}


def _commands() -> set[str]:
    for action in kit.parser()._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            return set(choices)
    return set()


def verify() -> list[str]:
    failures: list[str] = []
    receipt_path = ROOT / "installer/lifecycle-qualification-phase80-r2.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.get("schema") != "lapart.lifecycle-qualification.v1"
        or receipt.get("phase") != 80
        or receipt.get("revision") != 2
        or receipt.get("decision") != "PORTABLE_LIFECYCLE_QUALIFIED"
        or receipt.get("provider_calls") != 0
        or receipt.get("provider_tokens") != 0
        or receipt.get("pay_per_token") is not False
        or receipt.get("live_activation") != "unchanged"
        or receipt.get("service_restarts") != 0
    ):
        failures.append("lifecycle qualification receipt is invalid")
    if set(receipt.get("commands", [])) != REQUIRED_COMMANDS:
        failures.append("lifecycle command receipt is incomplete")
    if set(receipt.get("harnesses", [])) != REQUIRED_HARNESSES:
        failures.append("harness receipt is incomplete")
    if receipt.get("tests_passed") != 206 or receipt.get("clean_build_files") != 293:
        failures.append("lifecycle qualification measurements are invalid")
    if not REQUIRED_COMMANDS.issubset(_commands()):
        failures.append("portable lifecycle commands are not registered")
    if set(kit.HARNESS_ALIASES.values()) != REQUIRED_HARNESSES | {"generic"}:
        failures.append("portable harness aliases are incomplete")

    source_hashes = receipt.get("source_hashes", {})
    if set(source_hashes) != SOURCE_PATHS:
        failures.append("current-source hash inventory is incomplete or overbroad")
    for relative, expected in source_hashes.items():
        target = ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"current-source hash mismatch: {relative}")

    managed = json.loads((ROOT / "installer/contracts/managed-files.schema.json").read_text(encoding="utf-8"))
    if managed.get("required") != ["kit_version", "install_root", "data_root", "files"]:
        failures.append("v1 managed-manifest compatibility was removed")
    connection = json.loads((ROOT / "installer/contracts/harness-connection.schema.json").read_text(encoding="utf-8"))
    if connection.get("properties", {}).get("activation", {}).get("const") != "installed-not-activated":
        failures.append("connect contract no longer defaults to non-activation")
    if connection.get("properties", {}).get("provider_calls", {}).get("const") != 0:
        failures.append("connect contract no longer guarantees provider-free operation")
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: portable lifecycle qualification")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: install/connect/doctor/verify/upgrade/rollback/uninstall")
    print("PASS: 7 harness adapters, provider_calls=0, activation unchanged")
    print("PASS: content-addressed rollback and recipient-local preservation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
