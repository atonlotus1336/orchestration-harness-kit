import sys
from pathlib import Path

import pytest

from prime_bridge.prime_rpc_worker import PrimeRPCError, PrimeRPCWorker

ROOT = Path(__file__).resolve().parents[2]


class _Capture:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(**arguments):
            self.calls.append((name, arguments))
        return record


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
    assert events == [
        {"kind": "progress"},
        {
                "kind": "completed", "summary": "Prime agent completed",
                "model_usage": {
                    "input_units": 5, "output_units": 2,
                    "cache_read_units": 0, "cache_write_units": 0, "total_units": 7,
                    "source": "prime_rpc_session_stats",
                },
        },
    ]
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

    string_content = PrimeRPCWorker([sys.executable, "unused"], runtime_fingerprint="d" * 64)
    await string_content._events.put({"type": "agent_end", "messages": [{"role": "assistant", "content": "structured review", "stopReason": "stop"}]})
    assert [event async for event in string_content.events()] == [{"kind": "completed", "summary": "structured review"}]

    failed = PrimeRPCWorker([sys.executable, "unused"], runtime_fingerprint="d" * 64)
    await failed._events.put({"type": "agent_end", "messages": [{"role": "assistant", "content": [], "stopReason": "error"}]})
    assert [event async for event in failed.events()] == [{"kind": "failed", "error": "Prime agent failed"}]


@pytest.mark.asyncio
async def test_agent_end_failure_preserves_available_usage():
    worker = PrimeRPCWorker([sys.executable, "unused"], runtime_fingerprint="d" * 64)
    usage = {
        "input_units": 13, "output_units": 2, "total_units": 15,
        "source": "prime_rpc_session_stats",
    }

    async def session_usage():
        return usage

    worker._session_usage = session_usage
    await worker._events.put({
        "type": "agent_end",
        "messages": [{"role": "assistant", "content": [], "stopReason": "error"}],
    })
    assert [event async for event in worker.events()] == [{
        "kind": "failed", "error": "Prime agent failed", "model_usage": usage,
    }]


@pytest.mark.asyncio
async def test_turn_boundary_aborts_at_cumulative_token_limit_with_usage():
    worker = PrimeRPCWorker(
        [sys.executable, "unused"], runtime_fingerprint="d" * 64,
        max_total_tokens=40,
    )
    usage = {
        "input_units": 32, "output_units": 8, "total_units": 40,
        "source": "prime_rpc_session_stats",
    }
    requests = []

    async def session_usage():
        return usage

    async def request(kind, **_fields):
        requests.append(kind)
        return {"success": True}

    worker._session_usage = session_usage
    worker._request = request
    await worker._events.put({"type": "turn_end"})

    assert [event async for event in worker.events()] == [{
        "kind": "failed", "error": "Prime token budget exhausted",
        "model_usage": usage,
    }]
    assert requests == ["abort"]


@pytest.mark.asyncio
async def test_turn_boundary_fails_closed_when_token_accounting_is_missing():
    worker = PrimeRPCWorker(
        [sys.executable, "unused"], runtime_fingerprint="d" * 64,
        max_total_tokens=40,
    )
    requests = []

    async def session_usage():
        return None

    async def request(kind, **_fields):
        requests.append(kind)
        return {"success": True}

    worker._session_usage = session_usage
    worker._request = request
    await worker._events.put({"type": "turn_end"})

    assert [event async for event in worker.events()] == [{
        "kind": "failed", "error": "Prime token budget accounting unavailable",
    }]
    assert requests == ["abort"]


def test_session_usage_normalization_is_closed_and_exact():
    normalize = PrimeRPCWorker._normalized_model_usage
    assert normalize({
        "sessionId": "session-1", "tokens": {"input": 8, "output": 3, "total": 11},
        "toolCalls": 2,
    }, session_id="session-1") == {
        "input_units": 8, "output_units": 3,
        "cache_read_units": 0, "cache_write_units": 0, "total_units": 11,
        "source": "prime_rpc_session_stats",
    }
    assert normalize({
        "sessionId": "session-1",
        "tokens": {"input": 8, "output": 3, "cacheRead": 5, "cacheWrite": 2, "total": 18},
    }, session_id="session-1") == {
        "input_units": 8, "output_units": 3,
        "cache_read_units": 5, "cache_write_units": 2, "total_units": 18,
        "source": "prime_rpc_session_stats",
    }
    assert normalize({"sessionId": "other", "tokens": {"input": 8, "output": 3, "total": 11}}, session_id="session-1") is None
    assert normalize({"sessionId": "session-1", "tokens": {"input": 8, "output": 3, "total": 12}}, session_id="session-1") is None
    assert normalize({"sessionId": "session-1", "tokens": {"input": 8, "output": 3, "cacheRead": 5, "cacheWrite": 2, "total": 11}}, session_id="session-1") is None
    assert normalize({"sessionId": "session-1", "tokens": {"input": "8", "output": 3, "total": 11}}, session_id="session-1") is None


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


@pytest.mark.asyncio
async def test_rpc_visible_trajectory_is_ordered_and_credential_safe(tmp_path):
    credential_shape = "".join(("token", "=", "sentinel-runtime-credential"))
    capture = _Capture()
    worker = PrimeRPCWorker(
        [sys.executable, "unused"],
        runtime_fingerprint="d" * 64, trajectory_capture=capture,
    )
    worker._trajectory_session_id = "fixture-session"
    worker._trajectory_route = {"provider": "ollama-cloud", "model": "gpt-oss:120b-cloud"}
    worker._trajectory_messages = [{"role": "user", "content": "bounded visible task"}]
    await worker._events.put({"type": "agent_start"})
    await worker._events.put({
        "type": "message_end", "message": {
            "role": "assistant", "content": credential_shape,
        },
    })
    await worker._events.put({
        "type": "tool_execution_start", "toolCallId": "call-1",
        "toolName": "read", "args": {"path": "README.md"},
    })
    await worker._events.put({
        "type": "tool_execution_end", "toolCallId": "call-1",
        "toolName": "read", "result": "ok", "isError": False,
    })
    await worker._events.put({
        "type": "agent_end", "messages": [{"role": "assistant", "content": "done"}],
    })
    events = [event async for event in worker.events()]
    assert events[-1]["kind"] == "completed"
    names = [name for name, _arguments in capture.calls]
    assert names.index("model_request") < names.index("model_response")
    assert names.index("tool_call") < names.index("tool_result")
    response = next(arguments for name, arguments in capture.calls if name == "model_response")
    assert "sentinel-runtime-credential" in response["content"]  # sink owns durable redaction
