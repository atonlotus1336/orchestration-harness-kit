from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "modules/adapters/cursor"
PLUGIN = MODULE / "cursor-plugin"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def components():
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", "cursor_runner")
    manifest = runner.HarnessManifest.load(
        MODULE / "harness-manifest.json",
        catalog_path=ROOT / "modules/orchestration-protocol/operation-catalog.json",
    )
    adapter_module = runner._load_module(MODULE / manifest.adapter_entrypoint, "cursor_adapter_test")
    driver = runner._load_module(MODULE / "conformance_driver.py", "cursor_driver_test")
    adapter = adapter_module.CursorAdapter(manifest)
    bridge = runner.UniversalHarnessBridge(manifest=manifest, adapter=adapter, core=runner.build_core())
    return runner, adapter, driver, bridge


def run_hook(name: str, payload: dict, *args: str) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / name), *args],
        cwd=PLUGIN, input=json.dumps(payload), text=True, capture_output=True, check=False,
    )
    return result.returncode, json.loads(result.stdout)


def test_cursor_passes_all_operations_provider_free():
    runner, _, _, _ = components()
    result = runner.execute(MODULE)
    assert result["status"] == "PASS"
    assert result["operations_exercised"] == 31
    assert result["provider_calls"] == 0
    assert result["pay_per_token"] is False
    assert result["core_unchanged"] is True


def test_cursor_adapter_is_translation_only_and_closed():
    runner, adapter, driver, _ = components()
    assert runner.adapter_boundary_errors(MODULE / "cursor_adapter.py") == []
    event = driver.make_event("message.submit", {"role": "user", "content": "hello"}, "conversation.1", "active")
    assert set(event) == {"hook_event_name", "operation", "input", "conversation_id", "session_state"}
    event["model"] = "local-only"
    with pytest.raises(ValueError, match="keys are not closed"):
        adapter.translate_request(event)


def test_cursor_inherits_routing_optimization_and_accounting():
    _, _, driver, bridge = components()
    routing = bridge.dispatch(driver.make_event(
        "routing.prime.select", {"task": {"requires_omp": True, "text": "Implement the bounded change", "declared_paths": ["src/a.py", "src/b.py"], "risk_tier": "low", "verification_command_id": "pytest"}, "task_tier": "standard"},
        "conversation.1", "active",
    )).envelope["payload"]
    assert routing["recommended_lane"] == "T2"
    assert routing["actual_lane"] == "T3"

    items = json.loads((ROOT / "modules/orchestration-core/bootstrap-context.json").read_text(encoding="utf-8"))["items"]
    response = bridge.dispatch(driver.make_event(
        "optimization.plan",
        {"request_reference": "cursor-projection", "constraints": {
            "task_tier": "atomic", "items": items, "request_text": "Explain this result",
            "available_tools": ["read", "write", "mcp.memory", "skill.load"],
        }},
        "conversation.1", "active",
    ))
    assert response.provider_accounting["provider_calls"] == 0
    assert response.local_response["output"]["tool_projection"]["disabled_tools"] == ["write"]
    assert response.context_attribution["rendered_total_bytes"] >= 0


def test_cursor_plugin_contract_is_reference_only():
    manifest = json.loads((PLUGIN / ".cursor-plugin/plugin.json").read_text(encoding="utf-8"))
    hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))
    mcp = json.loads((PLUGIN / "mcp.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "lapart-orchestration"
    assert set(manifest["variables"]["required"]) == {
        "LAPART_PYTHON", "LAPART_KIT_ROOT", "LAPART_DATA_ROOT", "LAPART_TOKEN_COMPUTE_ACTIVATION",
    }
    assert hooks["hooks"]["beforeSubmitPrompt"][0]["failClosed"] is True
    assert hooks["hooks"]["beforeShellExecution"][0]["failClosed"] is True
    assert set(mcp["mcpServers"]) == {"memoryweaver"}
    text = json.dumps({"manifest": manifest, "hooks": hooks, "mcp": mcp}).casefold()
    assert "bearer " not in text
    assert "api_key" not in text
    assert "password" not in text


def test_cursor_hooks_initialize_optimize_and_gate_shell_provider_free(tmp_path):
    registry = tmp_path / "activation.json"
    activation = ROOT / "modules/token-compute-optimization/activate-token-compute.py"
    result = subprocess.run(
        [
            sys.executable, str(activation), "activate", "--registry", str(registry),
            "--harness", "all", "--approval-reference", "cursor-test", "--apply", "--yes",
        ],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    activation_args = ("--activation-registry", str(registry))

    code, start = run_hook("session_start.py", {"session_id": "conversation.1"}, *activation_args)
    assert code == 0 and "token-compute optimization are active" in start["additional_context"]

    code, admitted = run_hook("before_submit_prompt.py", {
        "conversation_id": "conversation.1", "prompt": "Review this result", "attachments": [],
    }, *activation_args)
    assert code == 0 and admitted == {"continue": True}

    code, blocked = run_hook("before_submit_prompt.py", {
        "conversation_id": "conversation.1", "prompt": "x" * 524289, "attachments": [],
    }, *activation_args)
    assert code == 0 and blocked["continue"] is False

    code, benign = run_hook("before_shell.py", {
        "conversation_id": "conversation.1", "command": "git status", "cwd": ".", "sandbox": True,
    })
    assert code == 0 and benign == {"permission": "allow"}

    code, risky = run_hook("before_shell.py", {
        "conversation_id": "conversation.1", "command": "git reset --hard", "cwd": ".", "sandbox": True,
    })
    assert code == 0 and risky["permission"] == "deny"


def test_cursor_installer_module_is_opt_in():
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entry = next(row for row in manifest["modules"] if row["id"] == "adapter-cursor")
    assert entry["stage"] == 4
    assert entry["required"] is False
    assert entry["source"] == "modules/adapters/cursor"
    assert "adapter-cursor" not in manifest["default_path"]
