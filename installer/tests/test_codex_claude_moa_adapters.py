from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "modules/adapters"
CASES = [
    ("codex", {"notification", "operation", "input", "thread"}),
    ("claude-code", {"hook_event_name", "operation", "tool_input", "session_id", "session_state"}),
    ("moa", {"phase", "operation", "content", "session"}),
]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def components(name: str):
    module = ADAPTERS / name
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", f"{name}_runner")
    manifest = runner.HarnessManifest.load(
        module / "harness-manifest.json",
        catalog_path=ROOT / "modules/orchestration-protocol/operation-catalog.json",
    )
    adapter_module = runner._load_module(module / manifest.adapter_entrypoint, f"{name}_adapter_test")
    driver = runner._load_module(module / "conformance_driver.py", f"{name}_driver_test")
    adapter = getattr(adapter_module, manifest.adapter_class)(manifest)
    bridge = runner.UniversalHarnessBridge(manifest=manifest, adapter=adapter, core=runner.build_core())
    return module, runner, adapter, driver, bridge


@pytest.mark.parametrize("name,_", CASES)
def test_adapter_passes_provider_free_conformance(name, _):
    module, runner, _, _, _ = components(name)
    result = runner.execute(module)
    assert result["status"] == "PASS"
    assert result["operations_exercised"] == 31
    assert result["provider_calls"] == 0
    assert result["pay_per_token"] is False
    assert result["core_unchanged"] is True


@pytest.mark.parametrize("name,_", CASES)
def test_adapter_is_translation_only(name, _):
    module, runner, _, _, _ = components(name)
    manifest = json.loads((module / "harness-manifest.json").read_text(encoding="utf-8"))
    path = module / manifest["adapter"]["entrypoint"]
    text = path.read_text(encoding="utf-8")
    assert runner.adapter_boundary_errors(path) == []
    for forbidden in ("lapart_core", "classify", "actual_lane", "max_provider_calls", "provider_model", "project_tools"):
        assert forbidden not in text
    assert len(text.splitlines()) < 80


@pytest.mark.parametrize("name,expected_keys", CASES)
def test_local_event_contract_is_closed(name, expected_keys):
    _, _, adapter, driver, _ = components(name)
    event = driver.make_event("message.submit", {"content": "hello"}, "session.1", "active")
    assert set(event) == expected_keys
    event["unexpected"] = True
    with pytest.raises(ValueError):
        adapter.translate_request(event)


@pytest.mark.parametrize("name,_", CASES)
def test_adapter_inherits_core_routing_and_projection(name, _):
    _, _, _, driver, bridge = components(name)
    routing = bridge.dispatch(driver.make_event(
        "routing.prime.select", {"task": {"requires_omp": True, "text": "Implement the bounded change", "declared_paths": ["src/a.py", "src/b.py"], "risk_tier": "low"}, "task_tier": "standard"}, "session.1", "active",
    )).envelope["payload"]
    assert routing["recommended_lane"] == "T2"
    assert routing["actual_lane"] == "T3"

    items = json.loads((ROOT / "modules/orchestration-core/bootstrap-context.json").read_text(encoding="utf-8"))["items"]
    projection = bridge.dispatch(driver.make_event(
        "optimization.plan",
        {"request_reference": f"{name}-projection", "constraints": {
            "task_tier": "atomic", "items": items, "request_text": "Explain this result",
            "available_tools": ["read", "write", "mcp.memory", "skill.load"],
        }},
        "session.1", "active",
    )).envelope["payload"]
    assert projection["tool_projection"]["selected_tools"] == ["read", "mcp.memory", "skill.load"]
    assert projection["tool_projection"]["disabled_tools"] == ["write"]
    assert projection["tool_projection"]["discovery_preserved"] is True
    assert projection["attribution"] == f"{name}-projection"


@pytest.mark.parametrize("name,_", CASES)
def test_response_preserves_accounting_and_context_attribution(name, _):
    _, _, _, driver, bridge = components(name)
    items = json.loads((ROOT / "modules/orchestration-core/bootstrap-context.json").read_text(encoding="utf-8"))["items"]
    response = bridge.dispatch(driver.make_event(
        "optimization.plan",
        {"request_reference": f"{name}-attribution", "constraints": {"task_tier": "atomic", "items": items}},
        "session.1", "active",
    )).local_response
    assert response["provider_accounting"]["provider_calls"] == 0
    attribution = response["context_attribution"]
    assert attribution["rendered_total_bytes"] >= 0
    assert attribution["saved_bytes"] >= 0


def test_reference_adapter_verifier_includes_phase78_matrix():
    verifier = load(ADAPTERS / "verify-adapters.py", "phase78_matrix_verifier")
    assert verifier.verify() == []


@pytest.mark.parametrize("name,_", CASES)
def test_installer_adapter_module_is_opt_in(name, _):
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entry = next(row for row in manifest["modules"] if row["id"] == f"adapter-{name}")
    assert entry["stage"] == 4
    assert entry["required"] is False
    assert entry["source"] == f"modules/adapters/{name}"
    assert f"adapter-{name}" not in manifest["default_path"]
