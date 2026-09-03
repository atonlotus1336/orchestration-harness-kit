#!/usr/bin/env python3
"""Independently verify the paired Phase 80 clean-room receipts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".planning" / "phases" / "80-clean-room-qualification"
WINDOWS_PATH = EVIDENCE / "receipts" / "windows-native-cd88f0a.json"
LINUX_PATH = EVIDENCE / "receipts" / "linux-wsl-ubuntu-restored-cd88f0a.json"
AGGREGATE_PATH = EVIDENCE / "qualification.json"
SOURCE_COMMIT = "cd88f0a829cb62a9e8647b0b40baeabf512a4ea2"
V1_COMMIT = "04b7630937557c2dd4e6b429fc033873d5edf127"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
CHECKS = {
    "clean-local-clone": {},
    "v1-ancestry": {},
    "source-verifiers": {"count": 6},
    "reproducible-payload": {"files": 293},
    "reproducible-bundle": {},
    "install-doctor-verify": {},
    "seven-harness-connect": {"count": 7},
    "v1-upgrade-rollback": {},
    "managed-uninstall": {},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_receipt(receipt: dict, platform: str, system: str) -> list[str]:
    failures: list[str] = []
    if receipt.get("schema") != "lapart.clean-room-platform.v1" or receipt.get("phase") != 80:
        failures.append(f"{platform}: schema or phase")
    if receipt.get("decision") != "CLEAN_ROOM_PASS":
        failures.append(f"{platform}: decision")
    if receipt.get("platform") != platform or receipt.get("runtime", {}).get("system") != system:
        failures.append(f"{platform}: platform identity")
    if receipt.get("source_commit") != SOURCE_COMMIT or receipt.get("v1_commit") != V1_COMMIT:
        failures.append(f"{platform}: source commit")
    checks = {item.get("name"): item for item in receipt.get("checks", [])}
    if set(checks) != set(CHECKS):
        failures.append(f"{platform}: check inventory")
    for name, expected in CHECKS.items():
        item = checks.get(name, {})
        if item.get("status") != "PASS" or any(item.get(key) != value for key, value in expected.items()):
            failures.append(f"{platform}: check {name}")
    if (
        receipt.get("provider_calls") != 0
        or receipt.get("provider_tokens") != 0
        or receipt.get("pay_per_token") is not False
        or receipt.get("service_restarts") != 0
        or receipt.get("live_activation") != "unchanged"
    ):
        failures.append(f"{platform}: safety accounting")
    artifacts = receipt.get("artifacts", {})
    if artifacts.get("payload_files") != 293:
        failures.append(f"{platform}: payload count")
    for name in ("payload_tree_sha256", "bundle_sha256", "release_manifest_sha256", "qualifier_sha256"):
        if not HASH_RE.fullmatch(str(artifacts.get(name, ""))):
            failures.append(f"{platform}: artifact hash {name}")
    return failures


def validate_documents(windows: dict, linux: dict, aggregate: dict, qualifier_hash: str, verifier_hash: str) -> list[str]:
    failures = validate_receipt(windows, "windows-native", "Windows")
    failures.extend(validate_receipt(linux, "linux-wsl-ubuntu-restored", "Linux"))
    if windows.get("artifacts") != linux.get("artifacts"):
        failures.append("cross-platform artifact mismatch")
    artifacts = windows.get("artifacts", {})
    if artifacts.get("qualifier_sha256") != qualifier_hash:
        failures.append("qualifier source hash mismatch")
    if (
        aggregate.get("schema") != "lapart.clean-room-aggregate.v1"
        or aggregate.get("phase") != 80
        or aggregate.get("decision") != "CROSS_PLATFORM_CLEAN_ROOM_QUALIFIED"
        or aggregate.get("source_commit") != SOURCE_COMMIT
    ):
        failures.append("aggregate identity or decision")
    if aggregate.get("platforms") != ["windows-native", "linux-wsl-ubuntu-restored"]:
        failures.append("aggregate platform inventory")
    if aggregate.get("artifacts") != artifacts:
        failures.append("aggregate artifact mismatch")
    if aggregate.get("verifier_sha256") != verifier_hash:
        failures.append("aggregate verifier hash mismatch")
    if (
        aggregate.get("provider_calls") != 0
        or aggregate.get("provider_tokens") != 0
        or aggregate.get("pay_per_token") is not False
        or aggregate.get("service_restarts") != 0
        or aggregate.get("live_activation") != "unchanged"
    ):
        failures.append("aggregate safety accounting")
    return failures


def verify() -> list[str]:
    windows = json.loads(WINDOWS_PATH.read_text(encoding="utf-8"))
    linux = json.loads(LINUX_PATH.read_text(encoding="utf-8"))
    aggregate = json.loads(AGGREGATE_PATH.read_text(encoding="utf-8"))
    failures = validate_documents(
        windows,
        linux,
        aggregate,
        sha256(ROOT / "tools" / "qualify-clean-room.py"),
        sha256(Path(__file__)),
    )
    receipt_hashes = aggregate.get("receipt_sha256", {})
    expected_receipts = {
        "windows-native": sha256(WINDOWS_PATH),
        "linux-wsl-ubuntu-restored": sha256(LINUX_PATH),
    }
    if receipt_hashes != expected_receipts:
        failures.append("platform receipt hash mismatch")
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: Phase 80 cross-platform evidence")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: Phase 80 Windows and Linux clean-room qualification")
    print("PASS: payload, manifest, and bundle hashes match")
    print("PASS: provider_calls=0, service_restarts=0, activation unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
