#!/usr/bin/env python3
"""Verify the immutable v1 baseline and additive v2 compatibility policy."""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "installer" / "contracts" / "version-policy.json"
PRIVACY_PATH = ROOT / "installer" / "contracts" / "v1-privacy-classification.json"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
V1_COMMANDS = {
    "build",
    "configure",
    "install",
    "doctor",
    "uninstall",
    "privacy-gate",
    "bundle",
}


def current_commands() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from installer.kit import parser

    actions = parser()._subparsers._group_actions
    return set(actions[0].choices)


def current_sdk_version() -> str:
    sys.path.insert(0, str(ROOT))
    from installer.kit import VERSION

    return VERSION


def validate(
    policy: dict[str, Any],
    commands: set[str],
    sdk_version: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if policy.get("schema") != "ohk.version-policy.v1":
        errors.append("unexpected version-policy schema")
    if sdk_version is not None and policy.get("sdk_release") != sdk_version:
        errors.append("installer version does not match the version policy")
    v1 = policy.get("v1", {})
    baseline = str(v1.get("baseline_commit", ""))
    if not COMMIT_RE.fullmatch(baseline):
        errors.append("v1 baseline must be a full Git object id")
    if v1.get("rollback_commit") != baseline:
        errors.append("v1 rollback commit must equal the immutable baseline")
    if v1.get("proposed_tag") != "v1.0.0":
        errors.append("unexpected v1 semantic-version tag proposal")
    if not str(v1.get("tag_status", "")).startswith("BLOCKED"):
        errors.append("v1 tag must remain blocked while source privacy is unresolved")
    declared_commands = set(v1.get("commands", []))
    if declared_commands != V1_COMMANDS:
        errors.append("declared v1 command contract changed")
    missing_commands = sorted(V1_COMMANDS - commands)
    if missing_commands:
        errors.append("current CLI removed v1 commands: " + ", ".join(missing_commands))
    if 1 not in v1.get("config_schemas", []):
        errors.append("config schema 1 is not supported")

    protocol = policy.get("protocol", {})
    if protocol.get("preferred") != 2 or protocol.get("supported") != [1, 2]:
        errors.append("protocol generations 1 and 2 must remain ordered and supported")
    if protocol.get("negotiation") != "highest_mutual":
        errors.append("protocol negotiation must select the highest mutual version")
    if protocol.get("no_mutual_version") != "structured_incompatibility_error":
        errors.append("protocol mismatch must fail with a structured error")

    migration = policy.get("migration", {})
    required_true = {"managed_files_only", "preserve_recipient_data", "preserve_credentials"}
    for key in sorted(required_true):
        if migration.get(key) is not True:
            errors.append(f"migration boundary {key} must be true")
    if migration.get("automatic_major_upgrade") is not False:
        errors.append("automatic major upgrades must remain disabled")
    return errors


def verify_git_object(commit: str) -> list[str]:
    if not (ROOT / ".git").exists():
        return []
    result = subprocess.run(
        ["git", "cat-file", "-t", commit],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != "commit":
        return ["v1 baseline does not resolve to a Git commit"]
    return []


def validate_privacy(classification: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if classification.get("schema") != "ohk.v1-privacy-classification.v1":
        errors.append("unexpected v1 privacy-classification schema")
    if classification.get("historical_repository_gate") != "BLOCKED":
        errors.append("historical repository HOLD must remain explicit")
    if classification.get("public_payload_gate") != "PASS":
        errors.append("public payload gate is not recorded as PASS")
    buckets = classification.get("buckets", [])
    if any(bucket.get("classification") != "EXCLUDE" for bucket in buckets):
        errors.append("historical findings need explicit EXCLUDE classifications")
    total = sum(int(bucket.get("count", 0)) for bucket in buckets)
    if total != classification.get("historical_finding_count") or total != 20:
        errors.append("privacy classification counts do not reconcile to 20")
    if classification.get("selected_file_exemptions") != 0:
        errors.append("selected public files may not receive privacy exemptions")
    if classification.get("scanner_rule_removals") != 0:
        errors.append("privacy scanner rules may not be removed")
    if classification.get("machine_identity_fallback") != "REMOVED_IN_V2_TREE":
        errors.append("machine identity fallback remediation is not recorded")
    return errors


def selftest(
    policy: dict[str, Any], classification: dict[str, Any], commands: set[str]
) -> list[str]:
    failures: list[str] = []
    cases: list[tuple[str, dict[str, Any], set[str]]] = []

    removed_command = set(commands)
    removed_command.remove("doctor")
    cases.append(("removed v1 command", copy.deepcopy(policy), removed_command))

    tag_misstatement = copy.deepcopy(policy)
    tag_misstatement["v1"]["tag_status"] = "READY"
    cases.append(("unsafe tag promotion", tag_misstatement, set(commands)))

    destructive_migration = copy.deepcopy(policy)
    destructive_migration["migration"]["preserve_recipient_data"] = False
    cases.append(("recipient data regression", destructive_migration, set(commands)))

    for name, candidate, candidate_commands in cases:
        if not validate(candidate, candidate_commands):
            failures.append(f"negative case unexpectedly passed: {name}")

    exempt_selected_file = copy.deepcopy(classification)
    exempt_selected_file["selected_file_exemptions"] = 1
    if not validate_privacy(exempt_selected_file):
        failures.append("negative case unexpectedly passed: selected-file exemption")

    mismatched_release = copy.deepcopy(policy)
    mismatched_release["sdk_release"] = "9.0.0"
    if not validate(mismatched_release, commands, current_sdk_version()):
        failures.append("negative case unexpectedly passed: release-version mismatch")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--privacy", type=Path, default=PRIVACY_PATH)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        classification = json.loads(args.privacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot load version policy: {exc}")
        return 2

    commands = current_commands()
    errors = validate(policy, commands, current_sdk_version())
    errors.extend(validate_privacy(classification))
    errors.extend(verify_git_object(str(policy.get("v1", {}).get("baseline_commit", ""))))
    if args.selftest:
        errors.extend(selftest(policy, classification, commands))
    if errors:
        print("BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: v1 compatibility and rollback policy")
    if args.selftest:
        print("PASS: 5 fail-closed negative cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
