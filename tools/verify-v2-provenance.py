#!/usr/bin/env python3
"""Provider-free validator for the public v2 provenance contract."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_SCHEMA = "ohk.v2-provenance.v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_CLASSES = {"SOUND", "HISTORICAL", "WEAK", "EXCLUDE"}
REQUIRED_EXCLUSIONS = {
    "credentials",
    "environment_files",
    "private_keys",
    "raw_provider_sessions",
    "transcripts",
    "private_memory",
    "machine_evidence",
    "caches",
    "generated_binaries",
    "dependency_directories",
    "bytecode_caches",
    "temporary_phase_directories",
    "backups",
    "real_usernames",
    "real_hostnames",
    "real_network_addresses",
    "machine_absolute_paths",
}
ZERO_BOUNDARIES = {
    "provider_calls",
    "service_restarts",
    "activation_changes",
    "default_model_changes",
    "tags_created",
    "pushes",
}


def validate(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema") != EXPECTED_SCHEMA:
        errors.append("unexpected schema")

    history = data.get("history_policy", {})
    if history.get("integration") != "ordinary_merge":
        errors.append("history integration must be ordinary_merge")
    if history.get("rewrite_allowed") is not False:
        errors.append("history rewriting must be disabled")

    baseline = data.get("v1_baseline", {})
    if not COMMIT_RE.fullmatch(str(baseline.get("commit", ""))):
        errors.append("v1 baseline commit must be a full Git object id")
    if baseline.get("provider_free_tests") != "PASS":
        errors.append("v1 provider-free tests are not recorded as PASS")
    if baseline.get("privacy_gate") != "BLOCKED":
        errors.append("v1 privacy HOLD must remain explicit")
    if int(baseline.get("privacy_finding_count", 0)) <= 0:
        errors.append("v1 blocked privacy gate requires a positive finding count")
    if not str(baseline.get("tag_status", "")).startswith("BLOCKED"):
        errors.append("v1 tag must remain blocked until privacy classification")

    sources = data.get("sources", [])
    if not sources:
        errors.append("at least one source is required")
    for index, source in enumerate(sources):
        classification = source.get("classification")
        if classification not in ALLOWED_CLASSES:
            errors.append(f"source {index} has an invalid classification")
        if source.get("import_from") == "working_tree":
            errors.append(f"source {index} imports mutable working-tree state")
        if classification == "SOUND":
            if source.get("import_from") != "git_commit":
                errors.append(f"sound source {index} must import from git_commit")
            if not COMMIT_RE.fullmatch(str(source.get("commit", ""))):
                errors.append(f"sound source {index} needs a full Git object id")

    exclusions = set(data.get("mandatory_exclusions", []))
    missing = sorted(REQUIRED_EXCLUSIONS - exclusions)
    if missing:
        errors.append("missing mandatory exclusions: " + ", ".join(missing))

    boundaries = data.get("hard_boundaries", {})
    for name in sorted(ZERO_BOUNDARIES):
        if boundaries.get(name) != 0:
            errors.append(f"hard boundary {name} must equal zero")
    if boundaries.get("production_activation") is not False:
        errors.append("production_activation must be false")
    return errors


def selftest(data: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    cases: list[tuple[str, dict[str, Any]]] = []

    missing_secret_exclusion = copy.deepcopy(data)
    missing_secret_exclusion["mandatory_exclusions"].remove("credentials")
    cases.append(("missing credential exclusion", missing_secret_exclusion))

    false_privacy_pass = copy.deepcopy(data)
    false_privacy_pass["v1_baseline"]["privacy_gate"] = "PASS"
    cases.append(("false v1 privacy pass", false_privacy_pass))

    mutable_import = copy.deepcopy(data)
    mutable_import["sources"][0]["import_from"] = "working_tree"
    cases.append(("mutable source import", mutable_import))

    for name, mutated in cases:
        if not validate(mutated):
            failures.append(f"negative case unexpectedly passed: {name}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--map",
        dest="map_path",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "docs"
        / "v2"
        / "provenance-map.json",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    try:
        data = json.loads(args.map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot load provenance map: {exc}")
        return 2

    errors = validate(data)
    if args.selftest:
        errors.extend(selftest(data))
    if errors:
        print("BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: v2 provenance contract")
    if args.selftest:
        print("PASS: 3 fail-closed negative cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
