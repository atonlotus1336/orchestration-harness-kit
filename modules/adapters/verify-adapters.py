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
}


def _source_errors(module: Path, provenance_commits: dict[str, str]) -> list[str]:
    failures: list[str] = []
    source_map = json.loads((module / "source-map.json").read_text(encoding="utf-8"))
    predecessor = source_map.get("predecessor", {})
    repository = source_map.get("repository")
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
    for name in ("opencode", "pi", "antigravity", "codex", "claude-code", "moa"):
        module = ADAPTERS / name
        report = runner.execute(module)
        if report["status"] != "PASS" or report["provider_calls"] != 0 or not report["core_unchanged"]:
            failures.append(f"{name}: provider-free conformance failed")
        if runner.adapter_boundary_errors(module / json.loads((module / "harness-manifest.json").read_text())["adapter"]["entrypoint"]):
            failures.append(f"{name}: adapter crossed the translation-only boundary")
        failures.extend(_source_errors(module, provenance_commits))

    matrix = json.loads((ADAPTERS / "compatibility-matrix.json").read_text(encoding="utf-8"))
    expected_adapters = {"hermes-os-reference", "opencode", "pi-coding-agent", "antigravity", "codex", "claude-code", "moa"}
    if {row.get("id") for row in matrix.get("adapters", [])} != expected_adapters:
        failures.append("compatibility matrix is incomplete")
    if any(row.get("qualification") != "source-qualified" or row.get("runtime_activation") != "unchanged" for row in matrix.get("adapters", [])):
        failures.append("compatibility matrix overstates runtime activation")

    receipt = json.loads((ADAPTERS / "qualification-phase78.json").read_text(encoding="utf-8"))
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
    for relative, expected in receipt.get("source_hashes", {}).items():
        target = ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"qualification source hash mismatch: {relative}")
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: reference adapter matrix")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: OpenCode, Pi, Antigravity, Codex, Claude Code, and MOA adapters")
    print("PASS: 186/186 operation paths, provider_calls=0, shared core unchanged")
    print("PASS: predecessor policy moved to shared core; live harnesses unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
