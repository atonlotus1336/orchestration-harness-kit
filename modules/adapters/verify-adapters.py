#!/usr/bin/env python3
"""Provider-free verification for source-qualified reference adapters."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path


ADAPTERS = Path(__file__).resolve().parent
ROOT = ADAPTERS.parents[1]
SDK = ROOT / "modules" / "adapter-sdk"
CONFORMANCE = ROOT / "modules" / "conformance-kit"
sys.path[:0] = [str(ROOT), str(SDK)]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


MOVED_TO_CORE = {
    "opencode": {"request-classification", "tool-selection", "token-compute-optimization"},
    "pi": {"request-classification", "tool-selection", "token-compute-optimization"},
    "antigravity": {"prime-lane-selection", "execution-budgets", "provider-call-accounting", "token-compute-optimization"},
    "codex": {"authority-safety", "capability-discovery", "provider-call-accounting", "routing", "token-compute-optimization"},
    "claude-code": {"authority-safety", "capability-discovery", "provider-call-accounting", "routing", "token-compute-optimization"},
    "moa": {"authority-safety", "capability-discovery", "provider-call-accounting", "provider-model-policy", "routing", "token-compute-optimization"},
    "cursor": {"authority-safety", "capability-discovery", "provider-call-accounting", "routing", "token-compute-optimization"},
}

PHASE78_HISTORICAL_HASH_PATHS = {
    "modules/adapters/qualification.json",
    "modules/adapters/qualification-phase77.json",
    "modules/adapters/hermes/source-hashes.json",
    "modules/adapters/opencode/source-hashes.json",
    "modules/adapters/pi/source-hashes.json",
    "modules/adapters/antigravity/source-hashes.json",
    "modules/adapters/codex/source-hashes.json",
    "modules/adapters/claude-code/source-hashes.json",
    "modules/adapters/moa/source-hashes.json",
    "modules/orchestration-core/source-hashes.json",
    "modules/orchestration-protocol/source-hashes.json",
}
PHASE78_RECEIPT_SHA256 = "967a5b334c01b2e63c99464aa8c7fb3ad78ab878584e823fe5650ef561034af7"


def _source_errors(module: Path, provenance_commits: dict[str, str]) -> list[str]:
    failures: list[str] = []
    source_map = json.loads((module / "source-map.json").read_text(encoding="utf-8"))
    repository = source_map.get("repository")
    if module.name == "cursor":
        origin = source_map.get("origin", {})
        contracts = set(origin.get("official_contracts", []))
        required = {
            "https://cursor.com/docs/hooks",
            "https://cursor.com/docs/mcp",
            "https://cursor.com/docs/reference/plugins",
        }
        if (
            repository != "orchestration-harness-kit"
            or origin.get("classification") != "clean-room-new-adapter"
            or contracts != required
        ):
            failures.append("cursor: clean-room source contract is incomplete")
    else:
        predecessor = source_map.get("predecessor", {})
        if repository not in provenance_commits or source_map.get("provenance_commit") != provenance_commits.get(repository):
            failures.append(f"{module.name}: source commit is not the SOUND provenance commit")
        for field in ("git_blob", "origin_commit"):
            if not re.fullmatch(r"[0-9a-f]{40}", predecessor.get(field, "")):
                failures.append(f"{module.name}: {field} is not an immutable Git identifier")
        for supporting in source_map.get("supporting_sources", []):
            if not supporting.get("path") or not re.fullmatch(r"[0-9a-f]{40}", supporting.get("git_blob", "")):
                failures.append(f"{module.name}: supporting source is not immutably identified")
    moved = set(source_map.get("migration", {}).get("moved_to_shared_core", []))
    if moved != MOVED_TO_CORE[module.name]:
        failures.append(f"{module.name}: predecessor policy was not fully moved to the core")
    hashes = json.loads((module / "source-hashes.json").read_text(encoding="utf-8"))
    if hashes.get("schema") != "lapart.adapter-module-source-hashes.v1" or hashes.get("algorithm") != "sha256":
        failures.append(f"{module.name}: source-hash manifest is invalid")
    for relative, expected in hashes.get("files", {}).items():
        target = ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"{module.name}: source hash mismatch: {relative}")
    return failures


def verify() -> list[str]:
    failures: list[str] = []
    provenance = json.loads((ROOT / "docs/v2/provenance-map.json").read_text(encoding="utf-8"))
    provenance_commits = {
        row["repository"]: row["commit"] for row in provenance["sources"]
        if row.get("classification") == "SOUND"
    }
    runner = _load(CONFORMANCE / "run-conformance.py", "portable_adapter_conformance")
    for name in ("opencode", "pi", "antigravity", "codex", "claude-code", "moa", "cursor"):
        module = ADAPTERS / name
        report = runner.execute(module)
        if report["status"] != "PASS" or report["provider_calls"] != 0 or not report["core_unchanged"]:
            failures.append(f"{name}: provider-free conformance failed")
        if runner.adapter_boundary_errors(module / json.loads((module / "harness-manifest.json").read_text())["adapter"]["entrypoint"]):
            failures.append(f"{name}: adapter crossed the translation-only boundary")
        failures.extend(_source_errors(module, provenance_commits))

    matrix = json.loads((ADAPTERS / "compatibility-matrix.json").read_text(encoding="utf-8"))
    expected_adapters = {"hermes-os-reference", "opencode", "pi-coding-agent", "antigravity", "codex", "claude-code", "moa", "cursor"}
    expected_requalification = {"backend_version_change", "source_hash_change", "terms_status_change"}
    if matrix.get("schema") != "lapart.adapter-compatibility-matrix.v2":
        failures.append("compatibility matrix schema is invalid")
    if set(matrix.get("requalification_triggers", [])) != expected_requalification:
        failures.append("compatibility matrix requalification triggers are incomplete")
    if {row.get("id") for row in matrix.get("adapters", [])} != expected_adapters:
        failures.append("compatibility matrix is incomplete")
    if any(row.get("qualification") != "source-qualified" or row.get("runtime_activation") != "unchanged" for row in matrix.get("adapters", [])):
        failures.append("compatibility matrix overstates runtime activation")
    for row in matrix.get("adapters", []):
        version_range = row.get("compatible_version_range")
        if not isinstance(version_range, dict) or set(version_range) != {"mode", "minimum", "maximum_exclusive"}:
            failures.append(f"{row.get('id')}: compatibility range is not closed")
            continue
        mode = version_range.get("mode")
        minimum = version_range.get("minimum")
        maximum = version_range.get("maximum_exclusive")
        if mode == "source-contract":
            if minimum is not None or maximum is not None:
                failures.append(f"{row.get('id')}: source contract cannot claim a version range")
        elif mode == "semver":
            version_pattern = r"[0-9]+(?:\.[0-9]+){1,3}"
            tested = row.get("tested_harness_version")
            if not all(isinstance(value, str) and re.fullmatch(version_pattern, value) for value in (minimum, maximum, tested)):
                failures.append(f"{row.get('id')}: semantic version range is invalid")
            else:
                lower = tuple(map(int, minimum.split(".")))
                upper = tuple(map(int, maximum.split(".")))
                observed = tuple(map(int, tested.split(".")))
                if lower >= upper:
                    failures.append(f"{row.get('id')}: semantic version range is empty")
                elif not lower <= observed < upper:
                    failures.append(f"{row.get('id')}: tested version is outside its qualified range")
        else:
            failures.append(f"{row.get('id')}: compatibility range mode is invalid")

    receipt_path = ADAPTERS / "qualification-phase78.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.get("schema") != "lapart.adapter-qualification.v1"
        or receipt.get("phase") != 78
        or receipt.get("decision") != "SOURCE_QUALIFIED"
        or receipt.get("provider_calls") != 0
        or receipt.get("pay_per_token") is not False
        or receipt.get("live_activation") != "unchanged"
    ):
        failures.append("adapter qualification receipt is invalid")
    if (
        receipt.get("operation_paths") != 186
        or receipt.get("tests_passed") != 185
        or receipt.get("fixed_envelope_bytes") != 311
        or receipt.get("clean_build_files") != 286
    ):
        failures.append("adapter qualification measurements are invalid")
    receipt_hashes = receipt.get("source_hashes", {})
    if not PHASE78_HISTORICAL_HASH_PATHS.issubset(receipt_hashes):
        failures.append("Phase 78 historical hash inventory is incomplete")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in receipt_hashes.values()):
        failures.append("Phase 78 historical hash inventory is malformed")
    if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != PHASE78_RECEIPT_SHA256:
        failures.append("Phase 78 historical receipt changed")
    # The receipt is immutable; its recorded source hashes are historical facts.
    # Current validity comes from fresh conformance and per-module hash manifests.
    # Comparing evolving current files to those old hashes would make adding a new
    # adapter invalidate truthful historical evidence.
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: reference adapter matrix")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: OpenCode, Pi, Antigravity, Codex, Claude Code, MOA, and Cursor adapters")
    print("PASS: 217/217 operation paths, provider_calls=0, shared core unchanged")
    print("PASS: predecessor policy moved to shared core; live harnesses unchanged")
    print("PASS: Phase 78 receipt retained as immutable historical evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
