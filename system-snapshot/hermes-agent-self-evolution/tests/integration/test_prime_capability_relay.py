import asyncio
import socket
import struct
from pathlib import Path

import pytest

from prime_bridge.capability_client import call
from prime_bridge.contained_worker import ContainedPrimeWorker
from prime_bridge.inference_relay import InferenceRelay, RelayPolicy


def test_capability_frames_share_existing_uds_without_network_or_identity_fields(tmp_path):
    observed = []

    async def handler(request):
        observed.append(request)
        return {"status": "ok", "result": {"items": 1}}

    async def scenario():
        relay = InferenceRelay(
            tmp_path / "relay.sock", RelayPolicy("127.0.0.1", ("127.0.0.1",), 11434),
            capability_handler=handler,
        )
        await relay.start()
        try:
            result = await asyncio.to_thread(
                call, relay.socket_path, "memory.search", {"query": "mount recovery"},
            )
            return result, relay.capability_accepted
        finally:
            await relay.close()
    result, accepted = asyncio.run(scenario())
    assert result == {"status": "ok", "result": {"items": 1}}
    assert observed == [{"alias": "memory.search", "arguments": {"query": "mount recovery"}}]
    assert accepted == 1


def test_capability_relay_rejects_identity_forgery_and_oversized_frames(tmp_path):
    async def handler(request):
        return {"status": "ok", "request": request}

    async def scenario():
        relay = InferenceRelay(
            tmp_path / "relay.sock", RelayPolicy("127.0.0.1", ("127.0.0.1",), 11434),
            capability_handler=handler, max_capability_bytes=128,
        )
        await relay.start()
        try:
            with pytest.raises(ValueError, match="identity"):
                await asyncio.to_thread(call, relay.socket_path, "memory.search", {}, job_id="x")
            def oversized():
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(relay.socket_path))
                    client.sendall(b"\x00\x00" + struct.pack("!I", 129) + b"x" * 129)
                    return client.recv(4)
            assert await asyncio.to_thread(oversized) == b""
            return relay.capability_rejected
        finally:
            await relay.close()
    assert asyncio.run(scenario()) == 1


def test_client_rejects_secret_shaped_arguments_before_socket_use(tmp_path):
    with pytest.raises(ValueError, match="secret"):
        call(tmp_path / "missing.sock", "memory.search", {"password": "never-send"})


def test_contained_worker_injects_admitted_job_identity_and_rejects_forgery():
    class Broker:
        async def handle(self, request):
            return request

    worker = ContainedPrimeWorker.__new__(ContainedPrimeWorker)
    worker.capability_broker = Broker()
    worker.job_id = "d" * 32
    worker.profile = "home"
    result = asyncio.run(worker._handle_capability({"alias": "memory.search", "arguments": {}}))
    assert result["job_id"] == "d" * 32 and result["profile"] == "home"
    rejected = asyncio.run(worker._handle_capability({
        "alias": "memory.search", "arguments": {}, "profile": "other",
    }))
    assert rejected["status"] == "error"


def test_capability_install_failure_does_not_expose_client_source():
    source = Path(__file__).resolve().parents[2] / "prime_bridge/capability_client.py"
    assert "capability client installation failed" not in source.read_text(encoding="utf-8")
    # The production worker catches the subprocess exception at the installation
    # boundary; the durable supervisor sees only this fixed message.
    worker_source = (Path(__file__).resolve().parents[2] / "prime_bridge/contained_worker.py").read_text(encoding="utf-8")
    assert 'raise RuntimeError("capability client installation failed") from None' in worker_source
