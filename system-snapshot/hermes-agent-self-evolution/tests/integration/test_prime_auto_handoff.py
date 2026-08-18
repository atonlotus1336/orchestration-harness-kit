import asyncio
import hashlib
from pathlib import Path

import pytest

import prime_bridge.auto_handoff as handoff_module
from prime_bridge.auto_handoff import auto_handoff
from prime_bridge.intent_router import IntentPolicy


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def portable_ponytail_fixture(monkeypatch):
    content = "# Ponytail\n"
    monkeypatch.setattr(handoff_module, "ponytail_snapshot", lambda: {
        "available": True,
        "path": "skills-shared/ponytail/skills/ponytail/SKILL.md",
        "intensity": "full",
        "fingerprint": hashlib.sha256(content.encode()).hexdigest(),
        "content": content,
    })


class Service:
    async def start_session(self, arguments):
        self.arguments = arguments
        return {"job_id": "job-1", "status": "queued"}


def test_auto_handoff_reuses_session_service_and_injects_ponytail():
    service = Service()
    result = asyncio.run(auto_handoff(
        service, message="audit phase 9", profile="workspace", mode="prime-default",
        session_id="session-1", workspace="__WSL_WORKSPACE__", project="WorkspaceKit",
        readiness=True, compatibility=True, admitted=True,
        policy=IntentPolicy.load(ROOT / "config/prime-auto-route-policy.json"),
    ))
    assert result["route"]["outcome"] == "PRIME_AUTO"
    assert result["job"]["job_id"] == "job-1"
    assert "# Ponytail" in service.arguments["prompt"]
    assert "## OhMyPi advisory" in service.arguments["prompt"]
    assert result["route"]["prime_advisory"]["recommendation"] == "DELEGATE_TO_PRIME"
    assert result["route"]["ohmypi_advisory"]["recommendation"] == "KEEP_PRIME_NATIVE"
    assert service.arguments["explicit_handoff"] is True


def test_optional_prime_language_preserves_gsd_route_and_returns_advisory_receipt():
    service = Service()
    result = asyncio.run(auto_handoff(
        service, message="Create a GSD plan if Prime is useful", profile="workspace", mode="prime-default",
        session_id="session-1", workspace="/workspace", project="example-project",
        readiness=True, compatibility=True, admitted=True,
        policy=IntentPolicy.load(ROOT / "config/prime-auto-route-policy.json"),
    ))
    assert result["route"]["outcome"] == "PRIME_AUTO"
    assert result["route"]["prime_advisory"] == {
        "requested": True,
        "recommendation": "DELEGATE_TO_PRIME",
        "reason": "bounded_operational_work",
        "family": "gsd_plan",
    }


def test_project_code_route_recommends_ohmypi_without_starting_it():
    service = Service()
    result = asyncio.run(auto_handoff(
        service, message="implement the feature if Prime is useful", profile="workspace", mode="prime-default",
        session_id="session-1", workspace="/workspace", project="example-project",
        readiness=True, compatibility=True, admitted=True,
        policy=IntentPolicy.load(ROOT / "config/prime-auto-route-policy.json"),
    ))
    assert result["route"]["outcome"] == "PRIME_CODE"
    assert result["route"]["ohmypi_advisory"] == {
        "recommendation": "CONSIDER_OHMYPI",
        "reason": "project_scoped_code_work",
        "approval_required": True,
        "auto_start": False,
    }


def test_auto_handoff_never_starts_prime_for_normal_chat():
    service = Service()
    result = asyncio.run(auto_handoff(
        service, message="What is Prime Agent?", profile="workspace", mode="prime-default",
        session_id="session-1", workspace="__WSL_WORKSPACE__", project="WorkspaceKit",
        readiness=True, compatibility=True, admitted=True,
        policy=IntentPolicy.load(ROOT / "config/prime-auto-route-policy.json"),
    ))
    assert result["route"]["outcome"] == "NATIVE" and result["job"] is None
    assert not hasattr(service, "arguments")
