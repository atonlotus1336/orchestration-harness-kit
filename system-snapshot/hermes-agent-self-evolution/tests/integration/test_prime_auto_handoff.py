import asyncio
from pathlib import Path

from prime_bridge.auto_handoff import auto_handoff
from prime_bridge.intent_router import IntentPolicy


ROOT = Path(__file__).resolve().parents[2]


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
    assert service.arguments["explicit_handoff"] is True


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
