import pytest

from prime_bridge.contained_worker import ContainedPrimeWorker


@pytest.mark.asyncio
async def test_local_ollama_route_uses_transient_placeholder_not_cloud_secret(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime.json"
    monkeypatch.setattr(
        "prime_bridge.contained_worker.load_runtime_authority",
        lambda _path: {"fingerprint": "a" * 64},
    )
    monkeypatch.setattr(
        "prime_bridge.contained_worker._resolve_probe_credential",
        lambda *_args, **_kwargs: ((), {"reason": "shared-local-provider"}),
    )
    captured = {}

    class Relay:
        def __init__(self, *_args, **_kwargs): pass
        async def start(self): pass
        async def close(self): pass

    class Sandbox:
        container_id = "container"
        def __init__(self, *_args, **_kwargs): pass
        def create(self, _command): return self.container_id
        def start(self): pass
        def teardown(self): return {"cleanup_verified": True}

    class Admission:
        def close(self): pass

    class RPC:
        def __init__(self, argv, *, environment, **_kwargs):
            captured["credential"] = environment["HERMES_PRIME_RUNTIME_API_KEY"]
            captured["argv"] = argv
        async def start(self, **_kwargs): return {"session_id": "s", "trace_ref": "t"}
        async def close(self): pass

    monkeypatch.setattr("prime_bridge.contained_worker.RelayPolicy.resolve", lambda *_args: object())
    monkeypatch.setattr("prime_bridge.contained_worker.InferenceRelay", Relay)
    monkeypatch.setattr("prime_bridge.contained_worker.admit", lambda _root: Admission())
    monkeypatch.setattr("prime_bridge.contained_worker.PodmanSandbox", Sandbox)
    monkeypatch.setattr("prime_bridge.contained_worker._write_prime_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("prime_bridge.contained_worker.PrimeRPCWorker", RPC)
    worker = ContainedPrimeWorker(
        repo_root=tmp_path, runtime_authority=runtime,
        prime_config={}, hermes_config=tmp_path / "config.yaml",
    )
    await worker.start(
        prompt="work", workspace=str(tmp_path),
        route={"provider": "hermes-ollama", "model": "m", "endpoint": "http://127.0.0.1:11434/v1"},
    )
    assert captured["credential"] == "ollama"
    assert "--no-session" not in captured["argv"]
    assert captured["argv"][captured["argv"].index("--session-dir") + 1] == "/tmp/.prime/agent/sessions"
    await worker.close()
