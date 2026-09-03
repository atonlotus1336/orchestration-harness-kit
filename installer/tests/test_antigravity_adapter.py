from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "modules/adapters"
MODULE = ADAPTERS / "antigravity"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def components():
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", "antigravity_runner")
    manifest = runner.HarnessManifest.load(
        MODULE / "harness-manifest.json",
        catalog_path=ROOT / "modules/orchestration-protocol/operation-catalog.json",
    )
    adapter_module = runner._load_module(MODULE / manifest.adapter_entrypoint, "antigravity_adapter_test")
    driver = runner._load_module(MODULE / "conformance_driver.py", "antigravity_driver_test")
    adapter = getattr(adapter_module, manifest.adapter_class)(manifest)
    bridge = runner.UniversalHarnessBridge(manifest=manifest, adapter=adapter, core=runner.build_core())
    return runner, manifest, adapter, driver, bridge


def test_antigravity_passes_provider_free_conformance():
    result = components()[0].execute(MODULE)
    assert result["status"] == "PASS"
    assert result["operations_exercised"] == 31
    assert result["provider_calls"] == 0
    assert result["pay_per_token"] is False
    assert result["core_unchanged"] is True


def test_antigravity_adapter_is_translation_only():
    runner, manifest, _, _, _ = components()
    path = MODULE / manifest.adapter_entrypoint
    text = path.read_text(encoding="utf-8")
    assert runner.adapter_boundary_errors(path) == []
    for forbidden in ("lapart_core", "classify", "actual_lane", "max_provider_calls", "project_tools"):
        assert forbidden not in text
    assert len(text.splitlines()) < 80


def test_antigravity_event_contract_fails_closed():
    _, _, adapter, _, _ = components()
    with pytest.raises(ValueError):
        adapter.translate_request({"command": "print", "operation": "message.submit"})


def test_antigravity_inherits_core_routing_and_projection():
    _, _, _, driver, bridge = components()
    routing = bridge.dispatch(driver.make_event(
        "routing.prime.select",
        {"task": {"requires_omp": True}, "task_tier": "standard"},
        "antigravity-session",
        "active",
    )).envelope["payload"]
    assert routing["recommended_lane"] == "T2"
    assert routing["actual_lane"] == "T3"

    items = json.loads((ROOT / "modules/orchestration-core/bootstrap-context.json").read_text(encoding="utf-8"))["items"]
    projection = bridge.dispatch(driver.make_event(
        "optimization.plan",
        {
            "request_reference": "antigravity-projection",
            "constraints": {
                "task_tier": "atomic",
                "items": items,
                "request_text": "Explain why this failed",
                "available_tools": ["read", "write", "mcp.memory", "skill.load"],
            },
        },
        "antigravity-session",
        "active",
    )).envelope["payload"]["tool_projection"]
    assert projection["selected_tools"] == ["read", "mcp.memory", "skill.load"]
    assert projection["disabled_tools"] == ["write"]
    assert projection["discovery_preserved"] is True


def test_reference_adapter_verifier_includes_antigravity():
    verifier = load(ADAPTERS / "verify-adapters.py", "antigravity_matrix_verifier")
    assert verifier.verify() == []


def test_antigravity_installer_module_is_opt_in():
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entry = next(row for row in manifest["modules"] if row["id"] == "adapter-antigravity")
    assert entry["stage"] == 4
    assert entry["required"] is False
    assert entry["source"] == "modules/adapters/antigravity"
    assert "adapter-antigravity" not in manifest["default_path"]
