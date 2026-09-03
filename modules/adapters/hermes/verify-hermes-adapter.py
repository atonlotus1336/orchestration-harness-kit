#!/usr/bin/env python3
"""Provider-free verification for the Hermes OS reference adapter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path


MODULE = Path(__file__).resolve().parent
ROOT = MODULE.parents[2]
SDK = ROOT / "modules" / "adapter-sdk"
CONFORMANCE = ROOT / "modules" / "conformance-kit"
sys.path.insert(0, str(SDK))
sys.path.insert(0, str(ROOT))

from lapart_adapter import HarnessManifest


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def verify() -> list[str]:
    failures: list[str] = []
    catalog = ROOT / "modules" / "orchestration-protocol" / "operation-catalog.json"
    manifest = HarnessManifest.load(MODULE / "harness-manifest.json", catalog_path=catalog)
    if len(manifest.operations) != 31:
        failures.append("manifest does not negotiate all 31 operations")

    runner = _load(CONFORMANCE / "run-conformance.py", "hermes_reference_conformance")
    report = runner.execute(MODULE)
    if report["status"] != "PASS" or report["provider_calls"] != 0 or not report["core_unchanged"]:
        failures.append("provider-free conformance failed")

    matrix = json.loads((MODULE / "profile-matrix.json").read_text(encoding="utf-8"))
    expected = {"home", "workspace", "nucbox-telegram", "vps-telegram"}
    rows = matrix.get("profiles", [])
    if {row.get("id") for row in rows} != expected:
        failures.append("protected Hermes profile set is incomplete")
    for row in rows:
        if row.get("qualification") != "source-qualified" or row.get("runtime_state") != "unchanged" or row.get("activation_changed") is not False:
            failures.append(f"profile state is not fail-closed: {row.get('id', 'unknown')}")

    contract = json.loads((MODULE / "reference-contract.json").read_text(encoding="utf-8"))
    required = {
        "actual-lane-routing", "authority-decision", "progressive-capability-discovery",
        "context-component-attribution", "fixed-envelope-regression-gate",
        "subscription-first-provider-resolution", "explicit-provider-call-accounting",
        "isolated-activation-transition", "deterministic-rollback",
    }
    if set(contract.get("core_invariants", [])) != required:
        failures.append("Hermes reference invariants are incomplete")
    if any(contract.get("installation_effect", {}).values()):
        failures.append("reference adapter claims a live installation side effect")

    provenance = json.loads((ROOT / "docs" / "v2" / "provenance-map.json").read_text(encoding="utf-8"))
    source_map = json.loads((MODULE / "source-map.json").read_text(encoding="utf-8"))
    expected_sources = {(row["repository"], row["commit"]) for row in provenance["sources"] if row.get("classification") == "SOUND"}
    mapped_sources = {(row.get("repository"), row.get("commit")) for row in source_map.get("sources", [])}
    if mapped_sources != expected_sources or any(not re.fullmatch(r"[0-9a-f]{40}", commit or "") for _, commit in mapped_sources):
        failures.append("source map does not match immutable SOUND provenance")

    hashes = json.loads((MODULE / "source-hashes.json").read_text(encoding="utf-8"))
    if hashes.get("schema") != "lapart.hermes-adapter-source-hashes.v1" or hashes.get("algorithm") != "sha256":
        failures.append("Hermes adapter source-hash manifest is invalid")
    for relative, expected_hash in hashes.get("files", {}).items():
        target = ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            failures.append(f"source hash mismatch: {relative}")
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: Hermes OS reference adapter")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: Hermes OS reference adapter")
    print("PASS: 4/4 protected profiles represented, 31/31 operations, provider_calls=0")
    print("PASS: live runtimes, profiles, providers, and activation unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
