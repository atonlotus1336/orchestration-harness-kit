import asyncio
from pathlib import Path

import pytest
import yaml

from prime_bridge.mcp_server import PrimeToolError, PrimeToolService, _tools


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/prime-profile-policy.json"


class SessionWorker:
    runtime_fingerprint = ""

    def __init__(self, runtime_fingerprint: str):
        self.runtime_fingerprint = runtime_fingerprint
        self.release = asyncio.Event()
        self.commands: list[dict] = []
        self.start_called = False

    async def start(self, **_kwargs):
        self.start_called = True
        return {"session_id": "prime-session", "trace_ref": "opaque-trace"}

    async def events(self):
        yield {"kind": "progress"}
        await self.release.wait()
        yield {"kind": "completed", "summary": "session complete"}

    async def command(self, **command):
        self.commands.append(command)
        if command["kind"] == "abort":
            self.release.set()

    async def close(self):
        return None


def write_profile(path: Path):
    path.write_text(yaml.safe_dump({
        "model": {"provider": "custom:ollama-cloud-local", "default": "deepseek-v4-flash:cloud"},
        "providers": {"ollama-cloud-local": {}}, "mcp_servers": {},
        "memory": {}, "toolsets": ["hermes-cli"],
    }), encoding="utf-8")


def service(tmp_path: Path, *, profile: str = "home", mode: str = "prime-available"):
    config = tmp_path / "config.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    write_profile(config)
    workers = {}

    def factory(job):
        worker = SessionWorker(job["runtime"]["fingerprint"])
        workers[job["job_id"]] = worker
        return worker

    value = PrimeToolService(
        profile=profile, mode=mode, policy_path=POLICY,
        ledger_path=tmp_path / "jobs.sqlite3", profile_config=config,
        allowed_roots=[tmp_path], worker_factory=factory,
    )
    return value, workspace, workers


async def wait_running(value: PrimeToolService, job_id: str):
    for _ in range(100):
        if value.ledger.get(job_id)["status"] == "running":
            return
        await asyncio.sleep(0)
    raise AssertionError("session did not enter running")


@pytest.mark.asyncio
async def test_session_start_returns_and_full_control_survives_detach(tmp_path):
    value, workspace, workers = service(tmp_path)
    started = await value.start_session({
        "prompt": "bounded work", "workspace": str(workspace), "project": "__PRIVATE_TOKEN_02__",
        "session_id": "hermes-one", "explicit_handoff": True,
        "max_iterations": 5, "no_progress_seconds": 30, "wall_seconds": 60,
    })
    job_id = started["job"]["job_id"]
    await wait_running(value, job_id)
    detached = value.session_control.detach(job_id, session_id="hermes-one")
    assert detached["attachment"] == "detached"
    assert value.ledger.get(job_id)["status"] == "running"
    stream = value.session_control.events(job_id, session_id="hermes-one", limit=3)
    continued = value.session_control.events(
        job_id, session_id="hermes-one", after_event_id=stream["next_cursor"],
    )
    assert all(event["event_id"] > stream["next_cursor"] for event in continued["events"])
    assert value.session_control.reattach(job_id, session_id="hermes-one")["attachment"] == "attached"
    queued = value.session_control.steer(job_id, session_id="hermes-one", text="focus verification")
    assert queued["status"] == "queued"
    interrupted = value.session_control.interrupt(job_id, session_id="hermes-one")
    assert interrupted["status"] == "queued"
    await value._session_tasks[job_id]
    assert value.ledger.get(job_id)["status"] == "cancelled"
    assert {command["kind"] for command in workers[job_id].commands} >= {"steer", "abort"}


@pytest.mark.asyncio
async def test_session_start_acknowledges_before_worker_gets_event_loop_turn(tmp_path):
    value, workspace, workers = service(tmp_path)
    started = await value.start_session({
        "prompt": "bounded work", "workspace": str(workspace), "project": "__PRIVATE_TOKEN_02__",
        "session_id": "hermes-ack", "explicit_handoff": True,
    })
    job_id = started["job"]["job_id"]
    assert job_id not in workers

    await asyncio.sleep(0)
    assert job_id in workers
    assert workers[job_id].start_called is True
    value.session_control.interrupt(job_id, session_id="hermes-ack")
    await value._session_tasks[job_id]


@pytest.mark.asyncio
async def test_duplicate_and_nonexplicit_session_handoffs_fail_closed(tmp_path):
    value, workspace, _workers = service(tmp_path)
    args = {
        "prompt": "bounded work", "workspace": str(workspace), "project": "__PRIVATE_TOKEN_02__",
        "session_id": "hermes-one", "explicit_handoff": True,
    }
    started = await value.start_session(args)
    job_id = started["job"]["job_id"]
    await wait_running(value, job_id)
    with pytest.raises(PrimeToolError, match="already has"):
        await value.start_session(args)
    second, second_workspace, _ = service(tmp_path / "second")
    with pytest.raises(PrimeToolError, match="explicit_handoff"):
        await second.start_session({**args, "workspace": str(second_workspace), "explicit_handoff": False})
    value.session_control.interrupt(job_id, session_id="hermes-one")
    await value._session_tasks[job_id]


@pytest.mark.asyncio
async def test_session_approval_is_bound_to_exact_job(tmp_path):
    value, workspace, _workers = service(tmp_path)
    first = await value.start_session({
        "prompt": "first", "workspace": str(workspace), "project": "one",
        "session_id": "hermes-one", "explicit_handoff": True,
    })
    one = first["job"]["job_id"]
    first_record = value.ledger.get(one)
    second = value.ledger.create(
        owner="test", profile="home", project="two", session_id="hermes-two",
        workspace=workspace, route=first_record["route"], runtime=first_record["runtime"],
        limits=first_record["limits"],
    )
    two = second["job_id"]
    decision = value.capability_policy.decision("wordpress.update")
    arguments = value.capability_policy.canonical_arguments({"post_id": 1})
    pending = value.capability_ledger.create(
        job_id=one, profile="home", alias="wordpress.update",
        decision=decision, arguments=arguments,
    )
    with pytest.raises(PrimeToolError, match="not bound"):
        value.approve_session({
            "job_id": two, "session_id": "hermes-two", "request_id": pending["request_id"],
            "operator_nonce": pending["operator_nonce"], "decision": "approved",
        })
    approved = value.approve_session({
        "job_id": one, "session_id": "hermes-one", "request_id": pending["request_id"],
        "operator_nonce": pending["operator_nonce"], "decision": "approved",
    })
    assert approved["status"] == "approved" and approved["job_id"] == one
    await wait_running(value, one)
    value.session_control.interrupt(one, session_id="hermes-one")
    await value._session_tasks[one]


def test_session_tool_schemas_are_closed_and_annotated():
    tools = {tool.name: tool for tool in _tools()}
    expected = {
        "prime_session_route", "prime_session_start", "prime_session_status",
        "prime_session_events", "prime_session_steer", "prime_session_approve",
        "prime_session_interrupt", "prime_session_detach", "prime_session_reattach",
    }
    assert expected <= set(tools)
    for name in expected:
        schema = tools[name].model_dump(by_alias=True)["inputSchema"]
        assert schema["additionalProperties"] is False
    assert tools["prime_session_route"].annotations.readOnlyHint is True
    assert tools["prime_session_events"].annotations.readOnlyHint is True
    assert tools["prime_session_approve"].annotations.destructiveHint is True
    assert tools["prime_session_interrupt"].annotations.destructiveHint is True
    start = tools["prime_session_start"].model_dump(by_alias=True)["inputSchema"]
    assert "explicit_handoff" in start["required"]
    assert "provider" not in start["properties"] and "model" not in start["properties"]
