from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "modules" / "orchestration-core"
sys.path.insert(0, str(MODULE))
sys.path.insert(0, str(ROOT))

from lapart_core import OrchestrationCore
from lapart_core.capabilities import Capability, CapabilityRegistry
from lapart_core.providers import ProviderRegistry, ProviderRoute


def make_core() -> OrchestrationCore:
    return OrchestrationCore.from_module(
        MODULE,
        capabilities=CapabilityRegistry(
            [Capability("portable.tool", "tool", "Portable tool", {"schema": {}})]
        ),
        providers=ProviderRegistry(
            [
                ProviderRoute("subscription", "model", "subscription", frozenset({"profile"})),
                ProviderRoute("paid", "model", "pay_per_token", frozenset({"profile"})),
            ]
        ),
        tool_handlers={"portable.tool": lambda arguments: arguments},
    )


def request(operation: str, payload: dict) -> dict:
    return {
        "protocol_version": 2,
        "message_id": "test-request",
        "kind": "request",
        "operation": operation,
        "harness": {"name": "test", "version": "1", "adapter_version": "2"},
        "session": {"id": "test-session", "state": "active"},
        "payload": payload,
    }


def test_every_catalog_operation_has_exactly_one_handler():
    core = make_core()
    catalog = json.loads((ROOT / "modules/orchestration-protocol/operation-catalog.json").read_text(encoding="utf-8"))
    assert set(core.handlers) == {row["name"] for row in catalog["operations"]}
    assert len(core.handlers) == 31


def test_t2_is_deterministically_mapped_to_actual_t3():
    response = make_core().handle(
        request("routing.prime.select", {"task": {"requires_omp": True, "text": "Implement the bounded change", "declared_paths": ["src/a.py", "src/b.py"], "risk_tier": "low"}, "task_tier": "atomic"})
    )
    assert response["payload"]["recommended_lane"] == "T2"
    assert response["payload"]["actual_lane"] == "T3"
    assert response["provider_accounting"]["provider_calls"] == 0


def test_adapter_policy_injection_fails_closed():
    response = make_core().handle(
        request("health.readiness", {"components": ["policy"], "policy_override": {"routing": "T0"}})
    )
    assert response["kind"] == "error"
    assert response["error"]["code"] == "POLICY_INJECTION_DENIED"


def test_credential_values_fail_closed_even_inside_arguments():
    response = make_core().handle(
        request(
            "tool.invoke",
            {
                "tool_id": "portable.tool",
                "arguments": {"password": "forbidden"},
                "approval_reference": "approval",
            },
        )
    )
    assert response["error"]["code"] == "CREDENTIAL_VALUE_DENIED"


def test_provider_resolution_excludes_unapproved_pay_per_token():
    response = make_core().handle(
        request(
            "provider.resolve",
            {
                "profile": "profile",
                "constraints": {"allowed_route_classes": ["subscription", "pay_per_token"]},
            },
        )
    )
    assert response["payload"]["route_class"] == "subscription"
    assert response["provider_accounting"] == {
        "provider_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "route_class": "provider_free",
        "pay_per_token": False,
    }


def test_required_authority_context_cannot_be_optimized_away():
    context = json.loads((MODULE / "bootstrap-context.json").read_text(encoding="utf-8"))
    context["items"] = [row for row in context["items"] if row["category"] != "authority_context"]
    response = make_core().handle(
        request(
            "optimization.plan",
            {
                "request_reference": "selection",
                "constraints": {"task_tier": "atomic", "items": context["items"]},
            },
        )
    )
    assert response["error"]["code"] == "REQUIRED_CONTEXT_MISSING"


def test_read_only_tool_projection_is_core_owned_and_preserves_discovery():
    context = json.loads((MODULE / "bootstrap-context.json").read_text(encoding="utf-8"))
    response = make_core().handle(
        request(
            "optimization.plan",
            {
                "request_reference": "read-only-selection",
                "constraints": {
                    "task_tier": "atomic",
                    "items": context["items"],
                    "request_text": "Explain why this test failed",
                    "available_tools": ["read", "write", "bash", "tool_search", "mcp.memory", "skill.load", "read"],
                },
            },
        )
    )
    projection = response["payload"]["tool_projection"]
    assert projection == {
        "mode": "read_only",
        "selected_tools": ["read", "tool_search", "mcp.memory", "skill.load"],
        "disabled_tools": ["write", "bash"],
        "discovery_preserved": True,
        "reason": "explicit-read-only",
    }
    assert response["provider_accounting"]["provider_calls"] == 0


def test_mutation_or_ambiguous_request_keeps_full_tool_set():
    context = json.loads((MODULE / "bootstrap-context.json").read_text(encoding="utf-8"))
    for text in ("Explain and fix this test", "Consider this module"):
        response = make_core().handle(
            request(
                "optimization.plan",
                {
                    "request_reference": text,
                    "constraints": {
                        "task_tier": "atomic",
                        "items": context["items"],
                        "request_text": text,
                        "available_tools": ["read", "write", "tool_search"],
                    },
                },
            )
        )
        assert response["payload"]["tool_projection"]["mode"] == "full"
        assert response["payload"]["tool_projection"]["selected_tools"] == ["read", "write", "tool_search"]


def test_partial_tool_projection_input_fails_closed():
    context = json.loads((MODULE / "bootstrap-context.json").read_text(encoding="utf-8"))
    response = make_core().handle(
        request(
            "optimization.plan",
            {
                "request_reference": "partial",
                "constraints": {
                    "task_tier": "atomic",
                    "items": context["items"],
                    "request_text": "Explain this",
                },
            },
        )
    )
    assert response["error"]["code"] == "OPTIMIZATION_CONSTRAINTS_INVALID"


def test_production_activation_is_disabled():
    response = make_core().handle(
        request(
            "activation.transition",
            {
                "profile": "isolated",
                "previous_state": "inactive",
                "requested_state": "production",
                "approval_reference": "approval",
                "gates": ["quality", "calls", "latency", "rollback", "tokens"],
                "rollback_reference": "rollback",
            },
        )
    )
    assert response["error"]["code"] == "PRODUCTION_ACTIVATION_DISABLED"


def test_message_submission_uses_envelope_session_with_multiple_sessions():
    core = make_core()
    for session_id in ("first", "second"):
        envelope = request("session.create", {"client_session_id": session_id})
        envelope["session"] = {"id": session_id, "state": "new"}
        assert core.handle(envelope)["kind"] == "response"
    envelope = request("message.submit", {"role": "user", "content": "second only"})
    envelope["session"]["id"] = "second"
    assert core.handle(envelope)["kind"] == "response"
    assert core.state.sessions["first"]["messages"] == []
    assert core.state.sessions["second"]["messages"][0]["content"] == "second only"


def test_unknown_health_component_fails_closed_to_degraded():
    response = make_core().handle(
        request("health.readiness", {"components": ["unknown-component"]})
    )
    assert response["payload"]["overall"] == "degraded"
    assert response["payload"]["components"]["unknown-component"] is False


def test_envelope_sentinel_detects_growth():
    spec = importlib.util.spec_from_file_location("envelope", MODULE / "check-envelope.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    budget = json.loads((MODULE / "envelope-budget.json").read_text(encoding="utf-8"))
    measured = module.measure(MODULE / "bootstrap-context.json")
    measured["rendered_total_bytes"] = 99999
    assert module.evaluate(measured, budget)


def test_core_verifier_is_provider_free():
    spec = importlib.util.spec_from_file_location("core_verifier", MODULE / "verify-core.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.verify_operations() == []


def test_module_manifest_installs_protocol_then_shared_core():
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entries = {row["id"]: row for row in manifest["modules"]}
    assert manifest["default_path"][:2] == ["orchestration-protocol", "orchestration-core"]
    assert entries["orchestration-core"]["required"] is True
    assert entries["orchestration-core"]["stage"] == 1
