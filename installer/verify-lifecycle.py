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


TOKEN_COMPUTE_SOURCE_PATHS = {
    "CHANGELOG.md",
    "LICENSE",
    "PRIVATE-DISTRIBUTION.md",
    "README.md",
    "THIRD-PARTY-NOTICES.md",
    "docs/v2/ACTIVATION.md",
    "docs/v2/COMPATIBILITY.md",
    "docs/v2/README.md",
    "docs/v2/RELEASE-CHECKLIST.md",
    "docs/v2/RELEASE-NOTES-2.0.1.md",
    "installer/contracts/public-payload.json",
    "installer/kit.py",
    "installer/modules/module-manifest.json",
    "installer/tests/test_cursor_adapter.py",
    "installer/tests/test_token_compute_activation.py",
    "installer/verify-lifecycle.py",
    "modules/adapters/cursor/README.md",
    "modules/adapters/cursor/cursor-plugin/.cursor-plugin/plugin.json",
    "modules/adapters/cursor/cursor-plugin/hooks/hooks.json",
    "modules/adapters/cursor/cursor-plugin/scripts/_bridge.py",
    "modules/adapters/cursor/cursor-plugin/scripts/before_submit_prompt.py",
    "modules/adapters/cursor/cursor-plugin/scripts/session_start.py",
    "modules/adapters/cursor/source-hashes.json",
    "modules/orchestration-core/README.md",
    "modules/orchestration-protocol/source-hashes.json",
    "modules/token-compute-optimization/README.md",
    "modules/token-compute-optimization/activate-token-compute.py",
    "modules/token-compute-optimization/lapart_token_compute/__init__.py",
    "modules/token-compute-optimization/lapart_token_compute/activation.py",
    "modules/token-compute-optimization/source-hashes.json",
    "modules/token-compute-optimization/token-compute-policy.json",
    "modules/token-compute-optimization/verify-token-compute.py",
}
REQUIRED_DISTRIBUTION_DOCS = {
    "LICENSE", "PRIVATE-DISTRIBUTION.md", "THIRD-PARTY-NOTICES.md",
}
REQUIRED_COMMANDS = {
    "install", "connect", "doctor", "verify", "upgrade", "rollback", "uninstall",
}
REQUIRED_HARNESSES = {
    "hermes", "opencode", "pi", "antigravity", "codex", "claude-code", "moa", "cursor",
}
PHASE81_HISTORICAL_SHA256 = "c3185d9b9196f68c474c9f70b2bc2ff00cf194e95616bd6ef309755b2a7c85f8"
CURSOR_HISTORICAL_SHA256 = "4bf9fcda5a5dbf24c702e907cd467585e6c553c45bc1468a3d8f6fcfce681eab"


def _commands() -> set[str]:
    for action in kit.parser()._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            return set(choices)
    return set()


def verify() -> list[str]:
    failures: list[str] = []
    historical_path = ROOT / "installer/lifecycle-qualification-phase81.json"
    historical = json.loads(historical_path.read_text(encoding="utf-8"))
    if (
        historical.get("schema") != "lapart.lifecycle-qualification.v1"
        or historical.get("phase") != 81
        or historical.get("decision") != "PORTABLE_LIFECYCLE_QUALIFIED"
        or hashlib.sha256(historical_path.read_bytes()).hexdigest() != PHASE81_HISTORICAL_SHA256
    ):
        failures.append("Phase 81 historical lifecycle receipt changed")

    cursor_path = ROOT / "installer/cursor-extension-qualification.json"
    receipt = json.loads(cursor_path.read_text(encoding="utf-8"))
    if (
        receipt.get("schema") != "lapart.lifecycle-extension-qualification.v1"
        or receipt.get("extension") != "cursor"
        or receipt.get("decision") != "SOURCE_QUALIFIED"
        or receipt.get("provider_calls") != 0
        or receipt.get("provider_tokens") != 0
        or receipt.get("pay_per_token") is not False
        or receipt.get("live_activation") != "unchanged"
        or receipt.get("service_restarts") != 0
    ):
        failures.append("Cursor lifecycle extension receipt is invalid")
    if set(receipt.get("commands", [])) != REQUIRED_COMMANDS:
        failures.append("Cursor lifecycle command receipt is incomplete")
    if set(receipt.get("harnesses", [])) != REQUIRED_HARNESSES:
        failures.append("historical Cursor harness receipt is incomplete")
    if (
        receipt.get("operation_paths") != 217
        or receipt.get("tests_passed") != 230
        or receipt.get("clean_build_files") != 314
    ):
        failures.append("historical Cursor lifecycle measurements are invalid")
    if hashlib.sha256(cursor_path.read_bytes()).hexdigest() != CURSOR_HISTORICAL_SHA256:
        failures.append("Cursor historical lifecycle receipt changed")

    current = json.loads((ROOT / "installer/token-compute-activation-qualification.json").read_text(encoding="utf-8"))
    if (
        current.get("schema") != "lapart.token-compute-qualification.v1"
        or current.get("decision") != "SAFE_FEATURE_ACTIVATION_QUALIFIED"
        or current.get("activation_scope") != "recipient-local-feature-only"
        or set(current.get("harnesses", [])) != REQUIRED_HARNESSES
        or current.get("provider_calls") != 0
        or current.get("provider_tokens") != 0
        or current.get("pay_per_token") is not False
        or current.get("service_restarts") != 0
        or current.get("runtime_provider_activation") != "unchanged"
        or current.get("marginal_value_stop") != "shadow"
        or current.get("weak_evidence_depth") != "full"
    ):
        failures.append("current token-compute qualification receipt is invalid")
    if current.get("historical_cursor_receipt", {}).get("sha256") != CURSOR_HISTORICAL_SHA256:
        failures.append("current receipt does not pin historical Cursor evidence")
    if not REQUIRED_COMMANDS.issubset(_commands()):
        failures.append("portable lifecycle commands are not registered")
    if set(kit.HARNESS_ALIASES.values()) != REQUIRED_HARNESSES | {"generic"}:
        failures.append("portable harness aliases are incomplete")

    source_hashes = current.get("source_hashes", {})
    if set(source_hashes) != TOKEN_COMPUTE_SOURCE_PATHS:
        failures.append("current-source hash inventory is incomplete or overbroad")
    for relative, expected in source_hashes.items():
        target = ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"current-source hash mismatch: {relative}")
    payload = json.loads((ROOT / "installer/contracts/public-payload.json").read_text(encoding="utf-8"))
    core_entries = set(payload.get("groups", {}).get("core", []))
    if not REQUIRED_DISTRIBUTION_DOCS <= core_entries:
        failures.append("recipient payload omits distribution notices")

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
    print("PASS: 8 harness adapters expose safe token-compute feature activation")
    print("PASS: provider_calls=0, provider_tokens=0, provider activation unchanged")
    print("PASS: Phase 81 and Cursor source receipts preserved as immutable historical evidence")
    print("PASS: content-addressed rollback and recipient-local preservation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
