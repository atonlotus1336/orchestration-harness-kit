#!/usr/bin/env python3
"""Provider-free verifier for the universal thin-adapter SDK."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent
KIT_ROOT = MODULE_ROOT.parents[1]
CORE_ROOT = MODULE_ROOT.parent / "orchestration-core"
PROTOCOL_ROOT = MODULE_ROOT.parent / "orchestration-protocol"
sys.path.insert(0, str(MODULE_ROOT))
sys.path.insert(0, str(CORE_ROOT))
sys.path.insert(0, str(KIT_ROOT))

from lapart_adapter import HarnessManifest, ManifestError, UniversalHarnessBridge
from lapart_adapter.reference import JsonReferenceAdapter
from lapart_core import OrchestrationCore


MANIFEST = MODULE_ROOT / "templates" / "harness-manifest.json"
CATALOG = PROTOCOL_ROOT / "operation-catalog.json"


def load_manifest(path: Path = MANIFEST) -> HarnessManifest:
    return HarnessManifest.load(path, catalog_path=CATALOG)


def event(operation: str, payload: dict, *, state: str = "active") -> dict:
    return {
        "operation": operation,
        "payload": payload,
        "session_id": "adapter-session",
        "session_state": state,
    }


def verify_bridge() -> list[str]:
    failures: list[str] = []
    manifest = load_manifest()
    core = OrchestrationCore.from_module(CORE_ROOT)
    bridge = UniversalHarnessBridge(
        manifest=manifest,
        adapter=JsonReferenceAdapter(manifest),
        core=core,
    )
    cases = [
        event("session.create", {"client_session_id": "adapter-session"}, state="new"),
        event("routing.prime.select", {"task": {"requires_omp": True}, "task_tier": "atomic"}),
        event("health.readiness", {"components": ["policy", "protocol", "accounting"]}),
    ]
    results = [bridge.initialize(session_id="adapter-lifecycle")]
    results.extend(bridge.dispatch(case) for case in cases)
    results.append(bridge.shutdown(session_id="adapter-session", reason="selftest"))
    if results[2].envelope["payload"].get("actual_lane") != "T3":
        failures.append("bridge did not preserve deterministic actual lane")
    for result in results:
        if result.provider_accounting != {
            "provider_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "route_class": "provider_free",
            "pay_per_token": False,
        }:
            failures.append("bridge result did not preserve provider-free accounting")
        if result.local_response.get("provider_accounting") != result.provider_accounting:
            failures.append("reference adapter omitted provider accounting")
    if bridge.negotiated.protocol_version != 2 or len(bridge.negotiated.operations) != 31:
        failures.append("manifest did not negotiate protocol v2 and all operations")
    return failures


def write_manifest(folder: Path, value: dict) -> Path:
    path = folder / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def verify_negative_cases() -> list[str]:
    original = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mutations = []
    unknown = copy.deepcopy(original)
    unknown["unexpected"] = True
    mutations.append(("unknown field", unknown, "MANIFEST_INVALID"))
    policy = copy.deepcopy(original)
    policy["core_policy"] = {"routing": "adapter-owned"}
    mutations.append(("core policy", policy, "MANIFEST_INVALID"))
    secret = copy.deepcopy(original)
    secret["configuration"]["authentication"]["token"] = "forbidden"
    mutations.append(("credential value", secret, "POLICY_OR_CREDENTIAL_DENIED"))
    traversal = copy.deepcopy(original)
    traversal["adapter"]["entrypoint"] = "../outside.py"
    mutations.append(("entrypoint traversal", traversal, "ADAPTER_PATH_INVALID"))
    operation = copy.deepcopy(original)
    operation["operations"]["exclude"] = ["unknown.operation"]
    mutations.append(("unknown operation", operation, "OPERATIONS_INVALID"))
    no_operations = copy.deepcopy(original)
    no_operations["operations"] = {"mode": "all", "include": [], "exclude": [row["name"] for row in json.loads(CATALOG.read_text(encoding="utf-8"))["operations"]]}
    mutations.append(("empty operations", no_operations, "OPERATIONS_INVALID"))
    failures = []
    with tempfile.TemporaryDirectory() as folder_name:
        folder = Path(folder_name)
        for name, value, expected in mutations:
            try:
                load_manifest(write_manifest(folder, value))
            except ManifestError as exc:
                if exc.code != expected:
                    failures.append(f"{name} failed with {exc.code}, expected {expected}")
            else:
                failures.append(f"{name} mutation passed")
    return failures


def verify_template_boundary() -> list[str]:
    template = MODULE_ROOT / "templates" / "adapter.py"
    tree = ast.parse(template.read_text(encoding="utf-8"), filename=str(template))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    if imports != {"__future__", "typing", "lapart_adapter"}:
        return ["adapter template imports a non-translation dependency"]
    forbidden_methods = {
        "select_route",
        "decide_authority",
        "resolve_provider",
        "optimize_context",
        "write_memory",
        "query_retrieval",
    }
    methods = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    return ["adapter template duplicates shared-core policy"] if methods.intersection(forbidden_methods) else []


def verify_source_hashes() -> list[str]:
    path = MODULE_ROOT / "source-hashes.json"
    if not path.is_file():
        return ["adapter SDK source-hash manifest is missing"]
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "lapart.adapter-sdk-source-hashes.v1" or value.get("algorithm") != "sha256":
        return ["adapter SDK source-hash manifest is invalid"]
    failures = []
    for relative, expected in value.get("files", {}).items():
        target = KIT_ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"source hash mismatch: {relative}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    failures = verify_bridge()
    failures.extend(verify_template_boundary())
    failures.extend(verify_source_hashes())
    if args.selftest:
        failures.extend(verify_negative_cases())
    if failures:
        print("BLOCKED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: universal harness adapter SDK")
    print("PASS: protocol v2, 31 negotiated operations, 5 bridge operations, provider_calls=0")
    print("PASS: adapter template contains translation methods only")
    if args.selftest:
        print("PASS: 6 manifest portability and policy-boundary mutations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
