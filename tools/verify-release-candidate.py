#!/usr/bin/env python3
"""Independently verify Phase 81 Windows, Linux/WSL, and Docker receipts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".planning" / "phases" / "81-documentation-release-readiness"
RECEIPTS = {
    "windows-native": EVIDENCE / "receipts" / "windows-native.json",
    "linux-wsl-ubuntu-restored": EVIDENCE / "receipts" / "linux-wsl-ubuntu-restored.json",
    "docker-linux": EVIDENCE / "receipts" / "docker-linux.json",
}
AGGREGATE_PATH = EVIDENCE / "qualification.json"
SOURCE_COMMIT = "1a81a4d86440dad3e14ee13619fb8198e61015d2"
V1_COMMIT = "04b7630937557c2dd4e6b429fc033873d5edf127"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PLATFORM_SYSTEM = {
    "windows-native": "Windows",
    "linux-wsl-ubuntu-restored": "Linux",
    "docker-linux": "Linux",
}
CHECKS = {
    "clean-local-clone": {},
    "v1-ancestry": {},
    "source-verifiers": {"count": 6},
    "reproducible-payload": {"files": 300},
    "reproducible-bundle": {},
    "install-doctor-verify": {},
    "seven-harness-connect": {"count": 7},
    "v1-upgrade-rollback": {},
    "managed-uninstall": {},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_receipt(receipt: dict, platform: str) -> list[str]:
    failures: list[str] = []
    if receipt.get("schema") != "lapart.clean-room-platform.v1" or receipt.get("phase") != 80:
        failures.append(f"{platform}: runner schema")
    if receipt.get("decision") != "CLEAN_ROOM_PASS":
        failures.append(f"{platform}: decision")
    if receipt.get("platform") != platform or receipt.get("runtime", {}).get("system") != PLATFORM_SYSTEM[platform]:
        failures.append(f"{platform}: identity")
    if receipt.get("source_commit") != SOURCE_COMMIT or receipt.get("v1_commit") != V1_COMMIT:
        failures.append(f"{platform}: source")
    checks = {item.get("name"): item for item in receipt.get("checks", [])}
    if set(checks) != set(CHECKS):
        failures.append(f"{platform}: check inventory")
    for name, expected in CHECKS.items():
        item = checks.get(name, {})
        if item.get("status") != "PASS" or any(item.get(key) != value for key, value in expected.items()):
            failures.append(f"{platform}: check {name}")
    artifacts = receipt.get("artifacts", {})
    if artifacts.get("payload_files") != 300:
        failures.append(f"{platform}: payload count")
    for key in ("payload_tree_sha256", "bundle_sha256", "release_manifest_sha256", "qualifier_sha256"):
        if not HASH_RE.fullmatch(str(artifacts.get(key, ""))):
            failures.append(f"{platform}: artifact {key}")
    if (
        receipt.get("provider_calls") != 0
        or receipt.get("provider_tokens") != 0
        or receipt.get("pay_per_token") is not False
        or receipt.get("service_restarts") != 0
        or receipt.get("live_activation") != "unchanged"
    ):
        failures.append(f"{platform}: safety accounting")
    return failures


def validate_documents(receipts: dict[str, dict], aggregate: dict, verifier_hash: str) -> list[str]:
    failures: list[str] = []
    for platform, receipt in receipts.items():
        failures.extend(validate_receipt(receipt, platform))
    artifacts = [receipt.get("artifacts") for receipt in receipts.values()]
    if not artifacts or any(item != artifacts[0] for item in artifacts[1:]):
        failures.append("cross-platform artifact mismatch")
    if (
        aggregate.get("schema") != "lapart.release-candidate-aggregate.v1"
        or aggregate.get("phase") != 81
        or aggregate.get("decision") != "CROSS_PLATFORM_RELEASE_CANDIDATE_QUALIFIED"
        or aggregate.get("source_commit") != SOURCE_COMMIT
    ):
        failures.append("aggregate identity")
    if aggregate.get("platforms") != list(RECEIPTS):
        failures.append("aggregate platform inventory")
    if artifacts and aggregate.get("artifacts") != artifacts[0]:
        failures.append("aggregate artifact mismatch")
    if aggregate.get("verifier_sha256") != verifier_hash:
        failures.append("aggregate verifier hash")
    expected_receipts = {name: sha256(path) for name, path in RECEIPTS.items()}
    if aggregate.get("receipt_sha256") != expected_receipts:
        failures.append("aggregate receipt hashes")
    publication = aggregate.get("publication", {})
    if (
        publication.get("client_payload") != "QUALIFIED"
        or publication.get("git_source") != "HOLD_PRIVATE_HISTORY"
        or publication.get("repository_visibility") != "PRIVATE"
        or publication.get("published") is not False
    ):
        failures.append("publication boundary")
    if aggregate.get("activation") != "READY_FOR_SEPARATE_AUTHORIZATION":
        failures.append("activation boundary")
    if (
        aggregate.get("provider_calls") != 0
        or aggregate.get("provider_tokens") != 0
        or aggregate.get("service_restarts") != 0
        or aggregate.get("live_activation") != "unchanged"
    ):
        failures.append("aggregate safety accounting")
    return failures


def verify() -> list[str]:
    receipts = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in RECEIPTS.items()}
    aggregate = json.loads(AGGREGATE_PATH.read_text(encoding="utf-8"))
    return validate_documents(receipts, aggregate, sha256(Path(__file__)))


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: Phase 81 release-candidate evidence")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: Windows, Linux/WSL, and Docker exact-commit qualification")
    print("PASS: 300-file payload and bundle hashes match across platforms")
    print("PASS: provider_calls=0, service_restarts=0, activation unchanged")
    print("READY: client release candidate; publication and activation separately held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
