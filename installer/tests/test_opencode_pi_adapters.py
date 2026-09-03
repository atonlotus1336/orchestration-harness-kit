from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "modules/adapters"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["opencode", "pi"])
def test_adapter_passes_all_provider_free_operations(name):
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", f"{name}_conformance")
    result = runner.execute(ADAPTERS / name)
    assert result["status"] == "PASS"
    assert result["operations_exercised"] == 31
    assert result["provider_calls"] == 0
    assert result["pay_per_token"] is False
    assert result["core_unchanged"] is True


@pytest.mark.parametrize("name", ["opencode", "pi"])
def test_adapter_is_thin_and_does_not_import_core(name):
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", f"{name}_boundary")
    manifest = json.loads((ADAPTERS / name / "harness-manifest.json").read_text(encoding="utf-8"))
    adapter = ADAPTERS / name / manifest["adapter"]["entrypoint"]
    assert runner.adapter_boundary_errors(adapter) == []
    text = adapter.read_text(encoding="utf-8")
    assert "lapart_core" not in text
    assert "classify" not in text
    assert "project_tools" not in text
    assert len(text.splitlines()) < 80


@pytest.mark.parametrize("name", ["opencode", "pi"])
def test_adapter_receives_identical_core_owned_tool_projection(name):
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", f"{name}_projection_runner")
    module = ADAPTERS / name
    manifest = runner.HarnessManifest.load(
        module / "harness-manifest.json",
        catalog_path=ROOT / "modules/orchestration-protocol/operation-catalog.json",
    )
    adapter_module = runner._load_module(module / manifest.adapter_entrypoint, f"{name}_projection_adapter")
    driver = runner._load_module(module / "conformance_driver.py", f"{name}_projection_driver")
    core = runner.build_core()
    adapter = getattr(adapter_module, manifest.adapter_class)(manifest)
    bridge = runner.UniversalHarnessBridge(manifest=manifest, adapter=adapter, core=core)
    items = json.loads((ROOT / "modules/orchestration-core/bootstrap-context.json").read_text(encoding="utf-8"))["items"]
    event = driver.make_event(
        "optimization.plan",
        {
            "request_reference": f"{name}-projection",
            "constraints": {
                "task_tier": "atomic",
                "items": items,
                "request_text": "Explain why this failed",
                "available_tools": ["read", "write", "mcp.memory", "skill.load"],
            },
        },
        f"{name}-session",
        "active",
    )
    projection = bridge.dispatch(event).envelope["payload"]["tool_projection"]
    assert projection["mode"] == "read_only"
    assert projection["selected_tools"] == ["read", "mcp.memory", "skill.load"]
    assert projection["disabled_tools"] == ["write"]
    assert projection["discovery_preserved"] is True


def test_local_event_contracts_fail_closed():
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", "event_contract_runner")
    for name in ("opencode", "pi"):
        module = ADAPTERS / name
        manifest = runner.HarnessManifest.load(
            module / "harness-manifest.json",
            catalog_path=ROOT / "modules/orchestration-protocol/operation-catalog.json",
        )
        adapter_module = load(module / manifest.adapter_entrypoint, f"{name}_adapter")
        adapter = getattr(adapter_module, manifest.adapter_class)(manifest)
        with pytest.raises(ValueError):
            adapter.translate_request({"operation": "message.submit"})


def test_reference_adapter_verifier_accepts_current_sources():
    verifier = load(ADAPTERS / "verify-adapters.py", "reference_adapter_verifier")
    assert verifier.verify() == []


def test_installer_adapters_are_opt_in_peers():
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entries = {row["id"]: row for row in manifest["modules"]}
    for name in ("adapter-opencode", "adapter-pi"):
        assert entries[name]["stage"] == 4
        assert entries[name]["required"] is False
        assert name not in manifest["default_path"]
