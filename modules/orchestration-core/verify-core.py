#!/usr/bin/env python3
"""Provider-free integration verifier for the shared orchestration core."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent
KIT_ROOT = MODULE_ROOT.parents[1]
PROTOCOL_ROOT = MODULE_ROOT.parent / "orchestration-protocol"
sys.path.insert(0, str(MODULE_ROOT))
sys.path.insert(0, str(KIT_ROOT))

from lapart_core import CorePolicy, OrchestrationCore
from lapart_core.capabilities import Capability, CapabilityRegistry
from lapart_core.providers import BackendContract, ProviderLease, ProviderRegistry, ProviderRoute


def load_protocol_verifier():
    spec = importlib.util.spec_from_file_location("lapart_protocol_verifier", PROTOCOL_ROOT / "verify-protocol.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("protocol verifier could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_core() -> OrchestrationCore:
    capabilities = CapabilityRegistry(
        [
            Capability("example.mcp", "mcp", "Example MCP server", {"capabilities": ["read"]}),
            Capability("example.tool", "tool", "Example deterministic tool", {"arguments": ["value"]}),
            Capability("example.skill", "skill", "Example portable skill", {"content": "Use deterministic evidence."}),
        ]
    )
    providers = ProviderRegistry(
        [
            ProviderRoute(
                "subscription-ref", "model-ref", "subscription", frozenset({"test"}),
                lease=ProviderLease(
                    auth_modes=frozenset({"subscription"}),
                    usage_contexts=frozenset({"internal"}),
                    retention_classes=frozenset({"standard"}),
                    compliance_classes=frozenset({"general"}),
                    terms_status="current",
                    terms_reference="urn:terms:test-subscription",
                ),
                backend=BackendContract(
                    backend_reference="test-native",
                    task_shapes=frozenset({"coding"}),
                    durable_resume=True,
                    estimated_overhead_ms=5,
                    backend_version="1.2.3",
                    minimum_version="1.2.0",
                    maximum_version_exclusive="2.0.0",
                ),
            ),
            ProviderRoute(
                "paid-ref", "paid-model-ref", "pay_per_token", frozenset({"test"}),
                lease=ProviderLease(
                    auth_modes=frozenset({"api_key"}),
                    usage_contexts=frozenset({"internal", "distributable_product"}),
                    retention_classes=frozenset({"standard"}),
                    compliance_classes=frozenset({"general"}),
                    terms_status="current",
                    terms_reference="urn:terms:test-api",
                ),
                backend=BackendContract(
                    backend_reference="test-sdk",
                    task_shapes=frozenset({"general"}),
                    estimated_overhead_ms=10,
                    backend_version="0.9.0",
                    minimum_version="0.9.0",
                    maximum_version_exclusive="1.0.0",
                ),
            ),
        ]
    )
    content = "Portable orchestration evidence."
    source = {
        "source_reference": "source-1",
        "uri": "urn:lapart:test-source",
        "title": "Portable orchestration",
        "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
    }
    return OrchestrationCore.from_module(
        MODULE_ROOT,
        capabilities=capabilities,
        providers=providers,
        tool_handlers={"example.tool": lambda arguments: {"echo": arguments.get("value")}},
        retrieval_sources=[source],
    )


def request(operation: str, payload: dict, *, state: str = "active", message_id: str | None = None) -> dict:
    return {
        "protocol_version": 2,
        "message_id": message_id or f"request-{operation}",
        "kind": "request",
        "operation": operation,
        "harness": {"name": "fake", "version": "1.0", "adapter_version": "2.0"},
        "session": {"id": "session-1", "state": state},
        "payload": payload,
    }


def operation_requests(context_items: list[dict]) -> list[dict]:
    hashes = {"source": hashlib.sha256(b"source").hexdigest()}
    gates = ["quality", "calls", "latency", "rollback", "tokens"]
    return [
        request("lifecycle.initialize", {"manifest": "fake", "supported_versions": [1, 2]}, state="new"),
        request("capabilities.negotiate", {"supported_versions": [2], "capabilities": ["tool.discover", "unknown"]}, state="new"),
        request("session.create", {"client_session_id": "session-1"}, state="new"),
        request("session.continue", {"session_id": "session-1"}),
        request("message.submit", {"role": "user", "content": "hello"}),
        request("message.stream", {"session_id": "session-1", "after_sequence": 0}),
        request("session.detach", {"session_id": "session-1", "last_sequence": 1}),
        request("session.reattach", {"session_id": "session-1", "reattach_reference": "DYNAMIC", "last_sequence": 1}, state="detached"),
        request("mcp.discover", {"query": "example"}),
        request("mcp.connect", {"server_id": "example.mcp", "config_reference": "local-ref", "approval_reference": "approval-test"}),
        request("tool.discover", {"query": "example"}),
        request("tool.invoke", {"tool_id": "example.tool", "arguments": {"value": "ok"}, "approval_reference": "approval-test"}),
        request("skill.discover", {"query": "portable"}),
        request("skill.load", {"skill_id": "example.skill"}),
        request("memory.write", {"scope": "session", "records": [{"note": "portable memory"}], "purpose": "test"}),
        request("memory.read", {"scope": "session", "query": "portable", "purpose": "test"}),
        request("retrieval.query", {"query": "orchestration", "purpose": "test"}),
        request("routing.prime.select", {"task": {"requires_omp": True, "text": "Implement the bounded change", "declared_paths": ["src/a.py", "src/b.py"], "risk_tier": "low"}, "task_tier": "standard"}),
        request("execution.omp.request", {"job_reference": "job-1", "task": "test", "context_reference": "context-1", "capability_limits": {"apply_changes": False}}),
        request("execution.omp.patch", {"worker_reference": "worker-job-1"}),
        request("authority.decide", {"action": "provider_call", "resource": "subscription", "risk": "high", "approval_reference": "approval-test"}),
        request("optimization.plan", {"request_reference": "selection-1", "constraints": {"task_tier": "atomic", "items": context_items}}),
        request("context.attribute", {"selection_reference": "selection-1"}),
        request("provider.resolve", {"profile": "test", "constraints": {
            "allowed_route_classes": ["subscription", "pay_per_token"],
            "requirements": {
                "task_shape": "coding", "durability_required": True,
                "latency_budget_ms": 20, "usage_context": "internal",
                "auth_mode": "subscription", "retention_class": "standard",
                "compliance_class": "general", "backend_version": "1.2.4",
                "require_current_terms": True,
            },
        }}),
        request("provider.account", {"operation_reference": "provider-free-run"}),
        request("telemetry.report", {"operation_reference": "test-run", "metrics": {
            "latency_ms": 3,
            "stage_timings_ms": {"routing": 1, "handoff": 2},
        }}),
        request("health.readiness", {"components": ["policy", "protocol", "accounting", "tool", "skill", "mcp"]}),
        request("activation.transition", {"profile": "isolated-test", "previous_state": "inactive", "requested_state": "shadow", "approval_reference": "approval-test", "gates": gates, "rollback_reference": "rollback-1"}),
        request("rollback.execute", {"rollback_reference": "rollback-1", "target_state": "inactive", "reason": "selftest"}),
        request("verification.run", {"subject_reference": "core", "gates": gates, "source_hashes": hashes}),
        request("lifecycle.shutdown", {"reason": "selftest"}),
    ]


def verify_operations() -> list[str]:
    protocol = load_protocol_verifier()
    catalog = protocol.load_json(PROTOCOL_ROOT / "operation-catalog.json")
    schema = protocol.load_json(PROTOCOL_ROOT / "envelope.schema.json")
    context = json.loads((MODULE_ROOT / "bootstrap-context.json").read_text(encoding="utf-8"))
    core = build_core()
    failures: list[str] = []
    covered: set[str] = set()
    for envelope in operation_requests(context["items"]):
        if envelope["operation"] == "session.reattach":
            envelope["payload"]["reattach_reference"] = core.state.sessions["session-1"]["reattach_reference"]
        response = core.handle(envelope)
        covered.add(envelope["operation"])
        if response["kind"] != "response":
            failures.append(f"{envelope['operation']} returned {response.get('error', {}).get('code', 'error')}")
            continue
        errors = protocol.validate_envelope(response, catalog, schema)
        if errors:
            failures.append(f"{envelope['operation']} response invalid: {'; '.join(errors)}")
        accounting = response.get("provider_accounting", {})
        if accounting.get("provider_calls") != 0 or accounting.get("pay_per_token") is not False:
            failures.append(f"{envelope['operation']} was not explicitly provider-free")
        if envelope["operation"] == "provider.resolve":
            provider = response.get("payload", {})
            if (
                provider.get("backend_fit", {}).get("status") != "FIT"
                or provider.get("lease", {}).get("terms_status") != "current"
                or provider.get("qualification", {}).get("status") != "qualified"
            ):
                failures.append("provider admission extensions were not returned")
    if covered != set(core.handlers):
        failures.append("not every core handler was exercised")
    return failures


def verify_negative_cases() -> list[str]:
    failures: list[str] = []
    context = json.loads((MODULE_ROOT / "bootstrap-context.json").read_text(encoding="utf-8"))
    cases = [
        ("policy injection", request("health.readiness", {"components": [], "policy_override": {}}), "POLICY_INJECTION_DENIED"),
        ("credential value", request("tool.invoke", {"tool_id": "example.tool", "arguments": {"api_key": "forbidden"}, "approval_reference": "approval"}), "CREDENTIAL_VALUE_DENIED"),
        ("production activation", request("activation.transition", {"profile": "isolated-test", "previous_state": "inactive", "requested_state": "production", "approval_reference": "approval", "gates": ["quality", "calls", "latency", "rollback", "tokens"], "rollback_reference": "rollback-production"}), "PRODUCTION_ACTIVATION_DISABLED"),
        ("required context missing", request("optimization.plan", {"request_reference": "bad", "constraints": {"task_tier": "atomic", "items": context["items"][1:]}}), "REQUIRED_CONTEXT_MISSING"),
        ("content-bearing telemetry", request("telemetry.report", {"operation_reference": "bad", "metrics": {"prompt": "forbidden"}}), "TELEMETRY_INVALID"),
        ("open provider requirements", request("provider.resolve", {"profile": "test", "constraints": {"requirements": {"unknown": True}}}), "PROVIDER_CONSTRAINT_INVALID"),
    ]
    for name, envelope, expected in cases:
        response = build_core().handle(envelope)
        if response.get("error", {}).get("code") != expected:
            failures.append(f"{name} mutation did not fail with {expected}")

    policy = json.loads((MODULE_ROOT / "core-policy.json").read_text(encoding="utf-8"))
    policy["pay_per_token_allowed"] = True
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        try:
            CorePolicy.load(path)
        except Exception:
            pass
        else:
            failures.append("pay-per-token policy mutation passed")

    policy = json.loads((MODULE_ROOT / "core-policy.json").read_text(encoding="utf-8"))
    policy["tool_projection"]["discovery_prefixes"].remove("skill")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        try:
            CorePolicy.load(path)
        except Exception:
            pass
        else:
            failures.append("capability-discovery removal mutation passed")

    envelope_module = load_envelope_module()
    budget = json.loads((MODULE_ROOT / "envelope-budget.json").read_text(encoding="utf-8"))
    measured = envelope_module.measure(MODULE_ROOT / "bootstrap-context.json")
    measured["rendered_total_bytes"] = int(budget["baseline"]["rendered_total_bytes"] * 1.06) + 1
    if not envelope_module.evaluate(measured, budget):
        failures.append("envelope-growth mutation passed")

    constraint_cases = [
        ("subscription redistribution", {
            "task_shape": "coding", "usage_context": "distributable_product",
            "auth_mode": "subscription",
        }),
        ("stale backend version", {
            "task_shape": "coding", "backend_version": "2.0.0",
        }),
        ("unmet latency budget", {
            "task_shape": "coding", "latency_budget_ms": 1,
        }),
    ]
    for name, requirements in constraint_cases:
        response = build_core().handle(request("provider.resolve", {
            "profile": "test",
            "constraints": {"allowed_route_classes": ["subscription"], "requirements": requirements},
        }))
        if response.get("error", {}).get("code") != "PROVIDER_CONSTRAINT_UNSATISFIED":
            failures.append(f"{name} was not refused by provider admission")
    return failures


def load_envelope_module():
    spec = importlib.util.spec_from_file_location("lapart_envelope_sentinel", MODULE_ROOT / "check-envelope.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("envelope sentinel could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_source_hashes() -> list[str]:
    path = MODULE_ROOT / "source-hashes.json"
    if not path.exists():
        return ["shared-core source-hash manifest is missing"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "lapart.core-source-hashes.v1" or manifest.get("algorithm") != "sha256":
        return ["shared-core source-hash manifest is invalid"]
    failures = []
    for relative, expected in manifest.get("files", {}).items():
        target = KIT_ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"source hash mismatch: {relative}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    failures = verify_operations()
    failures.extend(verify_source_hashes())
    envelope = load_envelope_module()
    measured = envelope.measure(MODULE_ROOT / "bootstrap-context.json")
    budget = json.loads((MODULE_ROOT / "envelope-budget.json").read_text(encoding="utf-8"))
    failures.extend(envelope.evaluate(measured, budget))
    if args.selftest:
        failures.extend(verify_negative_cases())
        spec = importlib.util.spec_from_file_location("governance_verifier", MODULE_ROOT / "verify-governance.py")
        governance = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(governance)
        failures.extend(governance.verify())
    if failures:
        print("BLOCKED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: shared orchestration core")
    print("PASS: 31/31 protocol operations exercised with explicit provider_calls=0")
    print(f"PASS: fixed envelope {measured['rendered_total_bytes']} bytes within 5% ceiling")
    if args.selftest:
        print("PASS: fail-closed policy, lease, compatibility, and latency mutations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
