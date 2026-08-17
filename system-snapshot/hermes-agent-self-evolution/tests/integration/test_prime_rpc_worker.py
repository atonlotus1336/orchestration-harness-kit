import sys
from pathlib import Path

import pytest

from prime_bridge.prime_rpc_worker import PrimeRPCError, PrimeRPCWorker

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_real_lf_jsonl_process_adapter(tmp_path):
    worker = PrimeRPCWorker([sys.executable, str(ROOT / "tests/fixtures/prime_rpc_stub.py")], runtime_fingerprint="d" * 64)
    opened = await worker.start(
        prompt="do work", workspace=str(tmp_path),
        route={"provider": "hermes-ollama", "model": "glm-5.2:cloud"},
    )
    assert opened == {"session_id": "stub-session", "trace_ref": "/tmp/stub.jsonl"}
    await worker.command(command_id="e" * 32, kind="steer", text="focus")
    events = [event async for event in worker.events()]
    assert events == [{"kind": "progress"}, {"kind": "completed", "summary": "Prime agent completed"}]
    await worker.close()


def test_canonical_factory_expands_only_the_container_placeholder():
    worker = PrimeRPCWorker.from_authority(
        ROOT / "config/prime-runtime-authority.json", container_id="container-123"
    )
    assert worker.argv[0:4] == ("podman", "exec", "-i", "container-123")
    assert worker.runtime_fingerprint


def test_authority_factory_reads_authority_once(monkeypatch):
    path = ROOT / "config/prime-runtime-authority.json"
    original = Path.read_text
    reads = 0

    def counted(self, *args, **kwargs):
        nonlocal reads
        if self == path:
            reads += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    PrimeRPCWorker.from_authority(path, container_id="container-123")
    assert reads == 1


@pytest.mark.asyncio
async def test_missing_session_identity_fails_closed(tmp_path):
    worker = PrimeRPCWorker([sys.executable, str(ROOT / "tests/fixtures/prime_rpc_stub.py")], runtime_fingerprint="d" * 64)

    async def response(kind, **_fields):
        return {"success": True, "data": {}} if kind == "get_state" else {"success": True}

    worker._request = response
    worker.process = object()
    with pytest.raises(PrimeRPCError, match="session identity"):
        await worker.start(prompt="work", workspace=str(tmp_path), route={"provider": "hermes-ollama", "model": "glm-5.2:cloud"})
    await worker.close()


@pytest.mark.asyncio
async def test_agent_end_exposes_bounded_outcome_and_failure():
    worker = PrimeRPCWorker([sys.executable, "unused"], runtime_fingerprint="d" * 64)
    await worker._events.put({"type": "agent_end", "messages": [{"role": "assistant", "content": [{"type": "text", "text": "finished safely"}], "stopReason": "stop"}]})
    assert [event async for event in worker.events()] == [{"kind": "completed", "summary": "finished safely"}]

    failed = PrimeRPCWorker([sys.executable, "unused"], runtime_fingerprint="d" * 64)
    await failed._events.put({"type": "agent_end", "messages": [{"role": "assistant", "content": [], "stopReason": "error"}]})
    assert [event async for event in failed.events()] == [{"kind": "failed", "error": "Prime agent failed"}]


@pytest.mark.asyncio
async def test_duplicate_request_id_and_unknown_command_fail_closed():
    worker = PrimeRPCWorker([sys.executable, "unused"], runtime_fingerprint="d" * 64)
    worker.process = type("Process", (), {"stdin": object()})()
    pending = __import__("asyncio").get_running_loop().create_future()
    worker._responses["e" * 32] = pending
    with pytest.raises(PrimeRPCError, match="already pending"):
        await worker._request("steer", request_id="e" * 32)
    with pytest.raises(PrimeRPCError, match="unsupported"):
        await worker.command(command_id="f" * 32, kind="prompt")
