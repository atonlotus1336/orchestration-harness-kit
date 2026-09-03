#!/usr/bin/env python3
"""Provider-free end-to-end conformance runner for a thin harness adapter."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parent
KIT_ROOT = MODULE_ROOT.parents[1]
SDK_ROOT = MODULE_ROOT.parent / "adapter-sdk"
CORE_ROOT = MODULE_ROOT.parent / "orchestration-core"
PROTOCOL_ROOT = MODULE_ROOT.parent / "orchestration-protocol"
sys.path.insert(0, str(SDK_ROOT))
sys.path.insert(0, str(CORE_ROOT))
sys.path.insert(0, str(KIT_ROOT))

from lapart_adapter import HarnessAdapter, HarnessManifest, UniversalHarnessBridge
from lapart_core import OrchestrationCore
from lapart_core.capabilities import Capability, CapabilityRegistry
from lapart_core.providers import ProviderRegistry, ProviderRoute


CATALOG_PATH = PROTOCOL_ROOT / "operation-catalog.json"
SUITE_PATH = MODULE_ROOT / "conformance-suite.json"
FAKE_HARNESS = MODULE_ROOT / "fake-harness"
FORBIDDEN_CORE_METHODS = {
    "activate_profile",
    "decide_authority",
    "optimize_context",
    "query_retrieval",
    "resolve_provider",
    "select_route",
    "write_memory",
}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load Python module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _protocol_verifier():
    return _load_module(PROTOCOL_ROOT / "verify-protocol.py", "lapart_conformance_protocol")


def adapter_boundary_errors(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [f"adapter source is unavailable or invalid: {type(exc).__name__}"]
    imports = set()
    methods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.add(node.name)
    errors = []
    if "lapart_core" in imports:
        errors.append("adapter imports the shared core directly")
    duplicated = methods.intersection(FORBIDDEN_CORE_METHODS)
    if duplicated:
        errors.append("adapter defines core-owned methods: " + ", ".join(sorted(duplicated)))
    return errors


def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if set(value) != {"schema", "protocol_version", "cases"} or value["schema"] != "lapart.conformance-suite.v1" or value["protocol_version"] != 2:
        raise ValueError("conformance suite header is invalid")
    cases = value["cases"]
    if not isinstance(cases, list) or any(
        not isinstance(case, dict)
        or set(case) != {"name", "operation", "state", "payload"}
        or not isinstance(case["payload"], dict)
        for case in cases
    ):
        raise ValueError("conformance cases are invalid")
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    expected = {row["name"] for row in catalog["operations"]}
    operations = [case["operation"] for case in cases]
    if len(operations) != len(set(operations)) or set(operations) != expected:
        raise ValueError("conformance suite must cover every operation exactly once")
    return value


def _replace(value: Any, *, core: OrchestrationCore) -> Any:
    if value == "$REATTACH":
        return core.state.sessions["conformance-session"]["reattach_reference"]
    if value == "$CONTEXT_ITEMS":
        return json.loads((CORE_ROOT / "bootstrap-context.json").read_text(encoding="utf-8"))["items"]
    if isinstance(value, dict):
        return {key: _replace(child, core=core) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace(child, core=core) for child in value]
    return value


def build_core() -> OrchestrationCore:
    capabilities = CapabilityRegistry(
        [
            Capability("fixture.mcp", "mcp", "Fixture MCP", {"capabilities": ["read"]}),
            Capability("fixture.tool", "tool", "Fixture tool", {"arguments": ["value"]}),
            Capability("fixture.skill", "skill", "Fixture skill", {"content": "Use portable evidence."}),
        ]
    )
    providers = ProviderRegistry(
        [
            ProviderRoute("subscription-reference", "model-reference", "subscription", frozenset({"fixture"})),
            ProviderRoute("paid-reference", "paid-model-reference", "pay_per_token", frozenset({"fixture"})),
        ]
    )
    content = "Portable conformance source."
    source = {
        "source_reference": "fixture-source",
        "uri": "urn:lapart:conformance",
        "title": "Portable conformance",
        "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
    }
    return OrchestrationCore.from_module(
        CORE_ROOT,
        capabilities=capabilities,
        providers=providers,
        tool_handlers={"fixture.tool": lambda arguments: {"echo": arguments.get("value")}},
        retrieval_sources=[source],
    )


def _core_hashes() -> dict[str, str]:
    manifest = json.loads((CORE_ROOT / "source-hashes.json").read_text(encoding="utf-8"))
    return {
        relative: hashlib.sha256((KIT_ROOT / relative).read_bytes()).hexdigest()
        for relative in sorted(manifest["files"])
    }


def local_response_errors(local: Any, accounting: dict[str, Any]) -> list[str]:
    if not isinstance(local, dict):
        return ["adapter local response must be an object"]
    if local.get("provider_accounting") != accounting:
        return ["adapter local response omitted or changed provider accounting"]
    return []


def execute(harness_dir: Path, suite_path: Path = SUITE_PATH) -> dict[str, Any]:
    harness_dir = harness_dir.resolve()
    manifest_path = harness_dir / "harness-manifest.json"
    manifest = HarnessManifest.load(manifest_path, catalog_path=CATALOG_PATH)
    entrypoint = (harness_dir / manifest.adapter_entrypoint).resolve()
    if harness_dir not in entrypoint.parents:
        raise ValueError("adapter entry point escaped the harness directory")
    boundary = adapter_boundary_errors(entrypoint)
    if boundary:
        raise ValueError("; ".join(boundary))
    adapter_module = _load_module(entrypoint, f"lapart_conformance_adapter_{manifest.fingerprint[:12]}")
    adapter_class = getattr(adapter_module, manifest.adapter_class, None)
    if not isinstance(adapter_class, type) or not issubclass(adapter_class, HarnessAdapter):
        raise ValueError("manifest adapter class does not implement HarnessAdapter")
    driver_path = harness_dir / "conformance_driver.py"
    driver = _load_module(driver_path, f"lapart_conformance_driver_{manifest.fingerprint[:12]}")
    if not callable(getattr(driver, "make_event", None)):
        raise ValueError("conformance driver must export make_event")

    suite = load_suite(suite_path)
    before = _core_hashes()
    core = build_core()
    adapter = adapter_class(manifest)
    adapter.initialize(manifest.config_reference, manifest.data_reference)
    bridge = UniversalHarnessBridge(manifest=manifest, adapter=adapter, core=core)
    protocol = _protocol_verifier()
    catalog = protocol.load_json(CATALOG_PATH)
    schema = protocol.load_json(PROTOCOL_ROOT / "envelope.schema.json")
    failures = []
    observations: dict[str, Any] = {}
    for case in suite["cases"]:
        payload = _replace(copy.deepcopy(case["payload"]), core=core)
        local_event = driver.make_event(case["operation"], payload, "conformance-session", case["state"])
        try:
            result = bridge.dispatch(local_event)
        except Exception as exc:  # noqa: BLE001 - report typed conformance failure
            failures.append(f"{case['operation']}: dispatch failed with {type(exc).__name__}")
            continue
        if result.envelope["kind"] != "response":
            failures.append(f"{case['operation']}: {result.envelope.get('error', {}).get('code', 'error')}")
            continue
        errors = protocol.validate_envelope(result.envelope, catalog, schema)
        failures.extend(f"{case['operation']}: {error}" for error in errors)
        failures.extend(f"{case['operation']}: {error}" for error in local_response_errors(result.local_response, result.provider_accounting))
        if result.provider_accounting.get("provider_calls") != 0 or result.provider_accounting.get("pay_per_token") is not False:
            failures.append(f"{case['operation']}: provider accounting is not zero")
        observations[case["operation"]] = result.envelope
    adapter.shutdown("conformance complete")
    after = _core_hashes()
    if before != after:
        failures.append("adapter modified shared-core source")

    expected_assertions = {
        "routing.prime.select": observations.get("routing.prime.select", {}).get("payload", {}).get("actual_lane") == "T3",
        "optimization.plan": "context_attribution" in observations.get("optimization.plan", {}),
        "context.attribute": "context_attribution" in observations.get("context.attribute", {}),
        "retrieval.query": bool(observations.get("retrieval.query", {}).get("payload", {}).get("sources")),
        "activation.transition": observations.get("activation.transition", {}).get("payload", {}).get("state") == "shadow",
        "rollback.execute": observations.get("rollback.execute", {}).get("payload", {}).get("restored_state") == "inactive",
        "provider.resolve": observations.get("provider.resolve", {}).get("payload", {}).get("route_class") == "subscription",
        "verification.run": observations.get("verification.run", {}).get("payload", {}).get("decision") == "PASS",
    }
    failures.extend(f"focused assertion failed: {name}" for name, passed in expected_assertions.items() if not passed)
    return {
        "status": "PASS" if not failures else "HOLD",
        "harness": manifest.name,
        "protocol_version": bridge.negotiated.protocol_version,
        "operations_exercised": len(observations),
        "provider_calls": core.accounting.snapshot()["provider_calls"],
        "pay_per_token": core.accounting.snapshot()["pay_per_token"],
        "core_unchanged": before == after,
        "failures": failures,
    }


def verify_source_hashes(value: dict[str, Any] | None = None) -> list[str]:
    manifest = value or json.loads((MODULE_ROOT / "source-hashes.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "lapart.conformance-source-hashes.v1" or manifest.get("algorithm") != "sha256":
        return ["conformance source-hash manifest is invalid"]
    failures = []
    for relative, expected in manifest.get("files", {}).items():
        target = KIT_ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"source hash mismatch: {relative}")
    return failures


def selftest() -> list[str]:
    failures = []
    adapter_path = FAKE_HARNESS / "fake_adapter.py"
    original = adapter_path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="lapart-conformance-mutations-") as folder_name:
        folder = Path(folder_name)
        temporary = folder / "mutation-adapter.py"
        temporary.write_text("from lapart_core import OrchestrationCore\n" + original, encoding="utf-8")
        if not adapter_boundary_errors(temporary):
            failures.append("direct core import mutation passed")
        temporary.write_text(original + "\ndef select_route():\n    return 'adapter-owned'\n", encoding="utf-8")
        if not adapter_boundary_errors(temporary):
            failures.append("adapter-owned routing mutation passed")

        suite = load_suite()
        missing = copy.deepcopy(suite)
        missing["cases"].pop()
        duplicate = copy.deepcopy(suite)
        duplicate["cases"].append(copy.deepcopy(duplicate["cases"][0]))
        for name, value in (("missing operation", missing), ("duplicate operation", duplicate)):
            mutation = folder / f"{name.replace(' ', '-')}.json"
            mutation.write_text(json.dumps(value), encoding="utf-8")
            try:
                load_suite(mutation)
            except ValueError:
                pass
            else:
                failures.append(f"{name} mutation passed")
    if not local_response_errors({}, {"provider_calls": 0}):
        failures.append("dropped accounting mutation passed")
    source_hashes = json.loads((MODULE_ROOT / "source-hashes.json").read_text(encoding="utf-8"))
    stale = copy.deepcopy(source_hashes)
    if stale.get("files"):
        stale["files"][next(iter(stale["files"]))] = "0" * 64
        if not verify_source_hashes(stale):
            failures.append("stale source hash mutation passed")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-dir", type=Path, default=FAKE_HARNESS)
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = execute(args.harness_dir, args.suite)
        hash_failures = verify_source_hashes()
        mutation_failures = selftest() if args.selftest else []
        report["failures"].extend(hash_failures + mutation_failures)
        report["status"] = "PASS" if not report["failures"] else "HOLD"
        report["mutation_cases"] = 6 if args.selftest else 0
    except Exception as exc:  # noqa: BLE001 - one structured fail-closed result
        report = {
            "status": "HOLD",
            "harness": args.harness_dir.name,
            "operations_exercised": 0,
            "provider_calls": 0,
            "pay_per_token": False,
            "core_unchanged": False,
            "failures": [f"{type(exc).__name__}: {exc}"],
            "mutation_cases": 0,
        }
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report["status"] == "PASS":
        print(f"PASS: {report['harness']} adapter conformance")
        print(f"PASS: {report['operations_exercised']}/31 operations, provider_calls=0, shared core unchanged")
        if args.selftest:
            print("PASS: 6 boundary, coverage, accounting, and provenance mutations")
    else:
        print("HOLD: adapter conformance failed")
        for failure in report["failures"]:
            print(f"- {failure}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
