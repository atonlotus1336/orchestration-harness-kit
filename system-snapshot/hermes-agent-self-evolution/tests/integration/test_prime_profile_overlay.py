import copy
import json
from pathlib import Path

import pytest
import yaml

from prime_bridge.profile_policy import (
    PrimeProfilePolicy,
    ProfilePolicyError,
    render_file,
    render_profile_overlay,
    restriction_projection,
    unmanaged_mcp_servers,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/prime-profile-policy.json"


def base_config():
    return {
        "model": {"provider": "custom:ollama-cloud-local", "default": "deepseek-v4-flash:cloud"},
        "providers": {"ollama-cloud-local": {"api_base": "http://127.0.0.1:11434/v1"}},
        "mcp_servers": {"memoryweaver": {"command": "python"}},
        "memory": {"memory_enabled": True},
        "toolsets": ["hermes-cli"],
        "platform_toolsets": {"telegram": ["hermes-telegram"]},
        "mcp_servers_disabled": {"wordpress": {"command": "python"}},
    }


def test_central_modes_make_telegram_prime_available():
    policy = PrimeProfilePolicy.load(POLICY)
    assert policy.mode_for("home") == "prime-available"
    assert policy.enabled is True
    assert policy.mode_for("gpt-5.4-codex") == "prime-available"
    assert policy.mode_for("telegram") == "prime-available"
    assert policy.permits("home") and policy.permits("telegram")


def test_overlay_preserves_global_orchestration_and_projects_restrictions(tmp_path):
    before = base_config()
    rendered, receipt = render_profile_overlay(
        before, profile="home", policy=PrimeProfilePolicy.load(POLICY),
        policy_path=POLICY, ledger_path=tmp_path / "jobs.sqlite3",
    )
    assert before == base_config()
    for key in ("providers", "memory", "toolsets"):
        assert rendered[key] == before[key]
    assert rendered["mcp_servers"]["memoryweaver"] == before["mcp_servers"]["memoryweaver"]
    server = rendered["mcp_servers"]["prime-agent"]
    assert server["trust"] == "full"
    assert server["env"]["HERMES_PRIME_PROFILE"] == "home"
    assert server["env"]["HERMES_PRIME_MODE"] == "prime-available"
    assert server["env"]["WSL_DISTRO_NAME"] == "__WSL_DISTRO__"
    assert receipt["explicit_handoff_required"] is True
    assert receipt["activation"] == "restart-boundary"
    assert receipt["restriction_fingerprint"] == restriction_projection(before)["fingerprint"]


def test_native_only_removes_stale_prime_entry_without_touching_telegram_ownership(tmp_path):
    policy_value = json.loads(POLICY.read_text(encoding="utf-8"))
    policy_value["profile_modes"]["telegram"] = "native-only"
    native_policy_path = tmp_path / "native-policy.json"
    native_policy_path.write_text(json.dumps(policy_value), encoding="utf-8")
    value = base_config()
    value["mcp_servers"]["prime-agent"] = {"command": "stale"}
    before_platform = copy.deepcopy(value["platform_toolsets"])
    rendered, receipt = render_profile_overlay(
        value, profile="telegram", policy=PrimeProfilePolicy.load(native_policy_path),
        policy_path=native_policy_path, ledger_path=tmp_path / "jobs.sqlite3",
    )
    assert "prime-agent" not in rendered["mcp_servers"]
    assert rendered["platform_toolsets"] == before_platform
    assert receipt["mode"] == "native-only" and receipt["prime_available"] is False


def test_render_file_is_restart_staged_and_contains_no_secret_values(tmp_path):
    source = tmp_path / "config.yaml"
    destination = tmp_path / "config.prime-next.yaml"
    receipt = tmp_path / "receipt.json"
    source.write_text(yaml.safe_dump(base_config(), sort_keys=False), encoding="utf-8")
    value = render_file(
        source, destination, profile="home", policy_path=POLICY,
        ledger_path=tmp_path / "jobs.sqlite3", receipt_path=receipt,
    )
    assert source.read_text(encoding="utf-8") != ""
    assert destination.exists() and value["activation"] == "restart-boundary"
    text = destination.read_text(encoding="utf-8")
    assert "API_KEY" not in text and "TOKEN" not in text
    assert json.loads(receipt.read_text())["native_fallback"] is True


def test_policy_is_closed_and_rejects_silent_conversion(tmp_path):
    value = json.loads(POLICY.read_text())
    value["explicit_handoff_required"] = False
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ProfilePolicyError, match="explicit handoff"):
        PrimeProfilePolicy.load(path)


def test_policy_rejects_prime_server_without_full_controller_contract(tmp_path):
    value = json.loads(POLICY.read_text())
    value["mcp_server"]["trust"] = "untrusted"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ProfilePolicyError, match="invalid managed Prime MCP server"):
        PrimeProfilePolicy.load(path)


def test_existing_managed_prime_entry_is_excluded_from_preservation_comparison():
    before = base_config()
    before["mcp_servers"]["prime-agent"] = {"command": "old", "trust": "full"}
    after = copy.deepcopy(before)
    after["mcp_servers"]["prime-agent"] = {"command": "new", "trust": "full"}
    assert unmanaged_mcp_servers(before["mcp_servers"]) == unmanaged_mcp_servers(after["mcp_servers"])


def test_global_emergency_disable_removes_prime_from_every_profile(tmp_path):
    value = json.loads(POLICY.read_text())
    value["enabled"] = False
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    policy = PrimeProfilePolicy.load(path)
    rendered, receipt = render_profile_overlay(
        base_config(), profile="home", policy=policy, policy_path=path,
        ledger_path=tmp_path / "jobs.sqlite3",
    )
    assert not policy.permits("home")
    assert "prime-agent" not in rendered["mcp_servers"]
    assert receipt["prime_available"] is False
