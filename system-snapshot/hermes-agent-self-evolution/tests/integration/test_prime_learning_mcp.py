import hashlib
from pathlib import Path

import pytest
import yaml

from prime_bridge.capability_broker import CapabilityBroker, CapabilityBrokerError
from prime_bridge.job_cli import load_runtime_authority
from prime_bridge.mcp_server import PrimeToolService

ROOT = Path(__file__).resolve().parents[2]


def service(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "model": {"provider": "custom:ollama-cloud-local", "default": "deepseek-v4-flash:cloud"},
        "providers": {"ollama-cloud-local": {}}, "mcp_servers": {}, "memory": {}, "toolsets": [],
    }), encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    value = PrimeToolService(
        profile="home", mode="prime-available",
        policy_path=ROOT / "config/prime-profile-policy.json",
        ledger_path=tmp_path / "jobs.sqlite3", profile_config=config,
        allowed_roots=[tmp_path], worker_factory=lambda _: None,
        learning_shared_root=tmp_path / "shared",
    )
    route = {
        "provider": "hermes-ollama", "model": "deepseek-v4-flash:cloud",
        "api": "openai-completions", "endpoint": "http://127.0.0.1:11434/v1",
    }
    route["fingerprint"] = value.ledger.route_fingerprint(route)
    job = value.ledger.create(
        owner="test", profile="home", project="__PRIVATE_TOKEN_02__", session_id="prime-session",
        workspace=workspace, route=route,
        runtime=load_runtime_authority(ROOT / "config/prime-runtime-authority.json"),
        limits={"max_iterations": 2, "no_progress_seconds": 10, "wall_seconds": 30},
    )
    broker = CapabilityBroker(
        value.capability_policy, value.capability_ledger, value.capability_adapter,
        job_ledger=value.ledger, internal_handlers={"prime-learning": value.learning_service.call},
    )
    return value, broker, job


@pytest.mark.asyncio
async def test_prime_records_local_learning_and_proposes_without_shared_write(tmp_path):
    value, broker, job = service(tmp_path)
    recorded = await broker.handle({
        "job_id": job["job_id"], "profile": "home", "alias": "learning.record",
        "arguments": {
            "scope": "project", "subject": "debug.gateway-binding",
            "content": "Gateway 8642 must bind to 0.0.0.0.", "privacy": "internal",
            "evidence": [{"kind": "probe", "reference": "health", "outcome": "passed"}],
        },
    })
    record_id = recorded["result"]["record_id"]
    proposed = await broker.handle({
        "job_id": job["job_id"], "profile": "home", "alias": "learning.propose",
        "arguments": {"record_id": record_id, "lane": "fact", "target_scope": "global"},
    })
    proposal_id = proposed["result"]["proposal_id"]
    assert value.learning_ledger.get_proposal(proposal_id)["status"] == "proposed"
    assert not (tmp_path / "shared").exists()
    assert value.learning_catalog()["records"][0]["content_hash"] == recorded["result"]["content_hash"]


@pytest.mark.asyncio
async def test_prime_cannot_supply_validation_but_hermes_validation_can_auto_promote(tmp_path):
    value, broker, job = service(tmp_path)
    recorded = await broker.handle({
        "job_id": job["job_id"], "profile": "home", "alias": "learning.record",
        "arguments": {
            "scope": "project", "subject": "debug.gateway-binding",
            "content": "Gateway 8642 must bind to 0.0.0.0.", "privacy": "internal",
            "evidence": [{"kind": "probe", "reference": "health", "outcome": "passed"}],
        },
    })
    with pytest.raises(CapabilityBrokerError, match="execution failed"):
        await broker.handle({
            "job_id": job["job_id"], "profile": "home", "alias": "learning.propose",
            "arguments": {
                "record_id": recorded["result"]["record_id"], "lane": "fact",
                "target_scope": "global", "validation": {"outcome": "passed"},
            },
        })
    proposed = await broker.handle({
        "job_id": job["job_id"], "profile": "home", "alias": "learning.propose",
        "arguments": {"record_id": recorded["result"]["record_id"], "lane": "fact", "target_scope": "global"},
    })
    promoted = value.validate_promotion({
        "proposal_id": proposed["result"]["proposal_id"], "mode": "independent",
        "outcome": "passed", "evidence_hash": hashlib.sha256(b"desktop-and-backend-probe").hexdigest(),
    })
    assert promoted["status"] == "promoted"
    assert (tmp_path / "shared" / promoted["artifact"]["artifact"]).is_file()


def test_learning_health_separates_local_and_shared_authority(tmp_path):
    value, _broker, _job = service(tmp_path)
    health = value.health()["learning"]
    assert health == {
        "local_ledger": "ready", "promotion_policy": "ready",
        "shared_authority": "hermes", "prime_direct_shared_write": False,
    }
