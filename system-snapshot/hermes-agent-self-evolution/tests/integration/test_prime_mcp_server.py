import json
from pathlib import Path

import pytest
import yaml

from prime_bridge.mcp_server import (
    PrimeToolError,
    PrimeToolService,
    _profile_config_for_request,
    _profile_route,
    _tools,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/prime-profile-policy.json"


class Worker:
    runtime_fingerprint = ""

    def __init__(self, runtime_fingerprint):
        self.runtime_fingerprint = runtime_fingerprint

    async def start(self, **_kwargs):
        return {"session_id": "prime-profile-session", "trace_ref": "/tmp/prime-profile.jsonl"}

    async def command(self, **_kwargs):
        return None

    async def close(self):
        return None

    async def events(self):
        yield {"kind": "progress"}
        yield {"kind": "completed", "summary": "bounded delegation complete"}


def write_profile(path: Path, *, model="deepseek-v4-flash:cloud"):
    path.write_text(yaml.safe_dump({
        "model": {"provider": "custom:ollama-cloud-local", "default": model},
        "providers": {"ollama-cloud-local": {}},
        "mcp_servers": {}, "memory": {}, "toolsets": ["hermes-cli"],
    }), encoding="utf-8")


@pytest.mark.asyncio
async def test_existing_home_profile_explicitly_delegates_bounded_job(tmp_path):
    config = tmp_path / "config.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_profile(config)
    holder = {}

    def factory(job):
        worker = Worker(job["runtime"]["fingerprint"])
        holder["worker"] = worker
        return worker

    service = PrimeToolService(
        profile="home", mode="prime-available", policy_path=POLICY,
        ledger_path=tmp_path / "jobs.sqlite3", profile_config=config,
        allowed_roots=[tmp_path], worker_factory=factory,
    )
    final = await service.delegate({
        "prompt": "perform a bounded research task", "workspace": str(workspace),
        "project": "existing-home-profile", "session_id": "native-session-kept",
        "explicit_handoff": True, "max_iterations": 4, "wall_seconds": 30,
        "no_progress_seconds": 10,
    })
    assert final["status"] == "completed"
    assert final["profile"] == "home" and final["session_id"] == "native-session-kept"
    events = service.ledger.events(final["job_id"])
    policy_event = next(item for item in events if item["kind"] == "profile_policy")
    assert policy_event["data"]["explicit_handoff"] is True
    assert policy_event["data"]["native_fallback"] is True


@pytest.mark.asyncio
async def test_handoff_is_mandatory(tmp_path):
    config = tmp_path / "config.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_profile(config, model="unverified-model:cloud")
    service = PrimeToolService(
        profile="home", mode="prime-available", policy_path=POLICY,
        ledger_path=tmp_path / "jobs.sqlite3", profile_config=config,
        allowed_roots=[tmp_path], worker_factory=lambda _job: None,
    )
    with pytest.raises(PrimeToolError, match="explicit_handoff"):
        await service.delegate({"prompt": "work", "workspace": str(workspace)})
    assert service.ledger.list() == []


def test_profile_default_model_uses_shared_verified_prime_controller(tmp_path):
    config = tmp_path / "config.yaml"
    write_profile(config, model="profile-specific-model:cloud")

    route, prime_config = _profile_route("specialist", config)

    assert route["model"] == "deepseek-v4-flash:cloud"
    assert route["provider"] == "hermes-ollama"
    assert prime_config["activeProfile"] == "specialist"
    assert prime_config["apiKey"] == {
        "source": "hermes-provider-config",
        "providerId": "ollama-cloud-local",
    }
    assert prime_config["models"][0]["id"] == "deepseek-v4-flash:cloud"


def test_shared_controller_requires_provider_in_selected_profile(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "model": {"provider": "openai-codex", "default": "gpt-5.4"},
        "providers": {"openai-codex": {}},
    }), encoding="utf-8")

    with pytest.raises(PrimeToolError, match="shared Prime controller provider"):
        _profile_route("codex-specialist", config)


def test_auto_handoff_resolves_selected_sibling_profile_config(tmp_path):
    profiles = tmp_path / "profiles"
    workspace = profiles / "workspace"
    specialist = profiles / "specialist"
    workspace.mkdir(parents=True)
    specialist.mkdir()
    write_profile(workspace / "config.yaml")
    write_profile(specialist / "config.yaml", model="profile-specific-model:cloud")

    assert _profile_config_for_request(workspace / "config.yaml", "specialist") == specialist / "config.yaml"
    with pytest.raises(PrimeToolError, match="canonical"):
        _profile_config_for_request(workspace / "config.yaml", "../escape")


def test_mcp_surface_is_one_bounded_control_plane():
    names = {tool.name for tool in _tools()}
    assert names == {
        "prime_delegate", "prime_job_status", "prime_job_list", "prime_job_steer",
        "prime_job_cancel", "prime_bridge_health", "prime_capability_catalog",
        "prime_capability_approvals", "prime_capability_resolve",
        "prime_learning_list", "prime_promotion_validate", "prime_promotion_resolve",
        "prime_promotion_revoke", "prime_promotion_expire",
        "prime_connectivity_status", "prime_connectivity_assess_change",
        "prime_session_route", "prime_session_start", "prime_session_status",
        "prime_session_events", "prime_session_steer", "prime_session_approve",
        "prime_session_interrupt", "prime_session_detach", "prime_session_reattach",
        "prime_auto_session_start", "prime_rollout_status",
    }
    delegate = next(tool for tool in _tools() if tool.name == "prime_delegate")
    schema = delegate.model_dump(by_alias=True)["inputSchema"]
    assert "explicit_handoff" in schema["required"]
    assert "provider" not in schema["properties"]
    assert "model" not in schema["properties"]
    resolver = next(tool for tool in _tools() if tool.name == "prime_capability_resolve")
    assert resolver.annotations.destructiveHint is True
    learning_list = next(tool for tool in _tools() if tool.name == "prime_learning_list")
    assert learning_list.annotations.readOnlyHint is True
    assert next(tool for tool in _tools() if tool.name == "prime_connectivity_status").annotations.readOnlyHint is True
    assert next(tool for tool in _tools() if tool.name == "prime_rollout_status").annotations.readOnlyHint is True
    auto_start = next(tool for tool in _tools() if tool.name == "prime_auto_session_start")
    auto_schema = auto_start.model_dump(by_alias=True)["inputSchema"]
    assert "profile" in auto_schema["required"]
    for name in ("prime_promotion_validate", "prime_promotion_resolve", "prime_promotion_revoke"):
        assert next(tool for tool in _tools() if tool.name == name).annotations.destructiveHint is True


def test_rollout_status_keeps_axes_and_native_fallback_visible(tmp_path):
    config = tmp_path / "config.yaml"
    write_profile(config)
    service = PrimeToolService(
        profile="home", mode="prime-available", policy_path=POLICY,
        ledger_path=tmp_path / "jobs.sqlite3", profile_config=config,
        allowed_roots=[tmp_path], worker_factory=lambda _job: None,
        resource_provider=lambda: {
            "status": "VERIFIED", "cpu_percent": 10, "memory_percent": 20,
            "sampled_at": "2026-08-12T23:59:00+00:00", "source": "controlled-test",
        },
    )
    result = service.rollout_status()
    assert set(result["axes"]) == {
        "bridge_liveness", "runtime_compatibility", "capability_readiness",
        "model_auth_readiness", "remote_client_reachability", "profile_rollout",
    }
    assert result["native_hermes"] == "AVAILABLE" and result["one_backend"] is True
    assert result["admission"]["native_fallback"] is True


def test_profile_cannot_observe_another_profiles_job(tmp_path):
    config = tmp_path / "config.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_profile(config)
    service = PrimeToolService(
        profile="home", mode="prime-available", policy_path=POLICY,
        ledger_path=tmp_path / "jobs.sqlite3", profile_config=config,
        allowed_roots=[tmp_path], worker_factory=lambda _job: None,
    )
    route = {"provider": "hermes-ollama", "model": "deepseek-v4-flash:cloud", "api": "openai-completions", "endpoint": "http://127.0.0.1:11434/v1"}
    route["fingerprint"] = service.ledger.route_fingerprint(route)
    from prime_bridge.job_cli import load_runtime_authority
    runtime = load_runtime_authority(ROOT / "config/prime-runtime-authority.json")
    record = service.ledger.create(
        owner="test", profile="other-profile", project="other", session_id="other-session",
        workspace=str(workspace), route=route, runtime=runtime,
        limits={"max_iterations": 2, "no_progress_seconds": 10, "wall_seconds": 30},
    )
    with pytest.raises(PrimeToolError, match="not owned"):
        service.owned_job(record["job_id"])


@pytest.mark.asyncio
async def test_global_emergency_disable_rejects_delegation(tmp_path):
    value = json.loads(POLICY.read_text())
    value["enabled"] = False
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(value))
    config = tmp_path / "config.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_profile(config)
    service = PrimeToolService(
        profile="home", mode="prime-available", policy_path=policy,
        ledger_path=tmp_path / "jobs.sqlite3", profile_config=config,
        allowed_roots=[tmp_path], worker_factory=lambda _job: None,
    )
    with pytest.raises(PrimeToolError, match="disabled"):
        await service.delegate({"prompt": "work", "workspace": str(workspace), "explicit_handoff": True})
