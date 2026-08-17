import asyncio
from pathlib import Path

import pytest

from prime_bridge.job_ledger import JobLedger
from prime_bridge.supervisor import PrimePolicy, PrimeSupervisor, SupervisorError

TEST_RUNTIME = {"source_commit": "a" * 40, "image_digest": "sha256:" + "b" * 64, "sandbox": "rootless-podman", "command_fingerprint": "c" * 64}
TEST_RUNTIME = {**TEST_RUNTIME, "fingerprint": JobLedger.runtime_fingerprint(TEST_RUNTIME)}

class Worker:
    runtime_fingerprint = TEST_RUNTIME["fingerprint"]
    def __init__(self, events):
        self._events = events
        self.commands = []

    async def start(self, **_kwargs):
        return {"session_id": "prime-1", "trace_ref": "trace-1"}

    async def events(self):
        for item in self._events:
            if "wait" in item:
                item["entered"].set()
                await item["wait"].wait()
            elif "delay" in item:
                await asyncio.sleep(item["delay"])
            else:
                yield item

    async def command(self, **command):
        self.commands.append(command)


class ClosableWorker(Worker):
    def __init__(self, events):
        super().__init__(events); self.closed = False; self.close_count = 0

    async def close(self):
        self.closed = True; self.close_count += 1


def job(tmp_path: Path, limits=None):
    root = tmp_path / "root"; root.mkdir(parents=True)
    ledger = JobLedger(tmp_path / "jobs.sqlite3", [root])
    public = {"provider": "hermes-ollama", "model": "glm-5.2:cloud", "api": "openai-completions", "endpoint": "http://relay"}
    record = ledger.create(owner="owner", profile="home", project="proj", session_id="hermes1", workspace=root, route={**public, "fingerprint": JobLedger.route_fingerprint(public)}, runtime=TEST_RUNTIME, limits=limits or {"max_iterations": 4, "no_progress_seconds": 2, "wall_seconds": 4})
    return ledger, record


def policy(record, **overrides):
    values = {"profile_modes": {record["profile"]: "prime-enabled"}, "allowed_route_fingerprints": frozenset({record["route"]["fingerprint"]}), "allowed_runtime_fingerprints": frozenset({record["runtime"]["fingerprint"]}), **overrides}
    return PrimePolicy(**values)


@pytest.mark.asyncio
async def test_completion_and_steering_are_durable(tmp_path):
    ledger, record = job(tmp_path)
    release_completion = asyncio.Event()
    completion_waiting = asyncio.Event()
    worker = Worker([
        {"kind": "progress"},
        {"wait": release_completion, "entered": completion_waiting},
        {"kind": "completed", "summary": "done"},
    ])
    supervisor = PrimeSupervisor(ledger, lambda _job: worker, policy(record))
    task = supervisor.submit(record["job_id"], "work")
    while ledger.get(record["job_id"])["status"] != "running":
        await asyncio.sleep(0)
    await completion_waiting.wait()
    command_id = await supervisor.steer(record["job_id"], "focus tests")
    release_completion.set()
    final = await task
    assert final["status"] == "completed" and final["result_summary"] == "done"
    assert worker.commands[0]["kind"] == "steer" and worker.commands[0]["command_id"] == command_id
    assert {event["kind"] for event in ledger.events(record["job_id"])} >= {"progress", "command_queued", "command_dispatching", "command_applied", "transition"}


@pytest.mark.asyncio
async def test_prompt_is_transient_redacted_and_secret_shaped_prompt_is_rejected(tmp_path):
    ledger, record = job(tmp_path)
    prompt = "transient prompt marker"
    worker = Worker([{"kind": "completed", "summary": f"result repeats {prompt}"}])
    final = await PrimeSupervisor(ledger, lambda _job: worker, policy(record)).run(record["job_id"], prompt)
    assert final["result_summary"] == "result repeats [prompt-redacted]"
    assert prompt.encode() not in ledger.path.read_bytes()

    ledger2, record2 = job(tmp_path / "secret")
    supervisor = PrimeSupervisor(ledger2, lambda _job: Worker([]), policy(record2))
    with pytest.raises(SupervisorError, match="safety boundary"):
        supervisor.submit(record2["job_id"], "sk-" + "x" * 24)


@pytest.mark.asyncio
async def test_iteration_limit_aborts_and_fails(tmp_path):
    ledger, record = job(tmp_path, {"max_iterations": 1, "no_progress_seconds": 2, "wall_seconds": 4})
    worker = Worker([{"kind": "progress"}])
    final = await PrimeSupervisor(ledger, lambda _job: worker, policy(record)).run(record["job_id"], "work")
    assert final["status"] == "failed" and final["last_error"] == "iteration limit"
    assert worker.commands[-1]["kind"] == "abort"


@pytest.mark.asyncio
async def test_no_progress_timeout_and_crash_are_terminal(tmp_path):
    ledger, record = job(tmp_path, {"max_iterations": 4, "no_progress_seconds": 1, "wall_seconds": 4})
    worker = Worker([{"delay": 2}])
    final = await PrimeSupervisor(ledger, lambda _job: worker, policy(record)).run(record["job_id"], "work")
    assert final["status"] == "failed" and final["last_error"] == "no-progress limit"

    ledger2, record2 = job(tmp_path / "two")
    final2 = await PrimeSupervisor(ledger2, lambda _job: Worker([{"kind": "failed", "error": "boom"}]), policy(record2)).run(record2["job_id"], "work")
    assert final2["status"] == "failed" and final2["last_error"] == "boom"


@pytest.mark.asyncio
async def test_cancel_and_admission_policy(tmp_path):
    ledger, record = job(tmp_path)
    supervisor = PrimeSupervisor(ledger, lambda _job: Worker([]), policy(record, enabled=False))
    with pytest.raises(SupervisorError): supervisor.submit(record["job_id"], "work")
    cancelled = await supervisor.cancel(record["job_id"])
    assert cancelled["status"] == "cancelled"


@pytest.mark.asyncio
async def test_unpinned_route_runtime_and_native_profile_fail_closed(tmp_path):
    ledger, record = job(tmp_path)
    for rejected in (
        PrimePolicy(allowed_runtime_fingerprints=frozenset({record["runtime"]["fingerprint"]})),
        PrimePolicy(allowed_route_fingerprints=frozenset({record["route"]["fingerprint"]})),
        policy(record, profile_modes={"home": "native-only"}),
    ):
        supervisor = PrimeSupervisor(ledger, lambda _job: Worker([]), rejected)
        with pytest.raises(SupervisorError):
            supervisor.submit(record["job_id"], "work")

    with pytest.raises(SupervisorError):
        PrimeSupervisor(ledger, lambda _job: Worker([]), PrimePolicy()).submit(record["job_id"], "work")


def test_admission_cap_protects_native_capacity(tmp_path):
    ledger, first = job(tmp_path)
    ledger.acquire(first["job_id"], "loop", 60); ledger.transition(first["job_id"], "starting")
    second = ledger.create(
        owner=first["owner"], profile=first["profile"], project=first["project"], session_id="hermes2",
        workspace=first["workspace"], route=first["route"], runtime=first["runtime"], limits=first["limits"],
    )
    supervisor = PrimeSupervisor(ledger, lambda _job: Worker([]), policy(first, max_active=1))
    with pytest.raises(SupervisorError, match="admission limit"):
        supervisor.submit(second["job_id"], "work")
    health = supervisor.health()
    assert health["prime_protocol"] == "v0.7.1-rpc"
    assert health["metrics"]["active"] == 1
    assert health["active_jobs"][0]["resource_usage"] == {"status": "not-sampled"}
    assert "workspace" not in health["active_jobs"][0]


@pytest.mark.asyncio
async def test_detached_cli_commands_are_consumed_and_worker_closes(tmp_path):
    ledger, record = job(tmp_path)
    worker = ClosableWorker([{"delay": .3}])
    supervisor = PrimeSupervisor(ledger, lambda _job: worker, policy(record))
    task = supervisor.submit(record["job_id"], "work")
    await asyncio.sleep(.03)
    steer_id = ledger.enqueue_command(record["job_id"], "steer", "focus")
    await asyncio.sleep(.15)
    assert any(command["command_id"] == steer_id for command in worker.commands)
    cancel_id = ledger.enqueue_command(record["job_id"], "cancel")
    final = await task
    assert final["status"] == "cancelled" and ledger.pending_commands(record["job_id"]) == []
    assert cancel_id and worker.closed and worker.close_count == 1


@pytest.mark.asyncio
async def test_timeout_cancels_event_reader_and_closes_once(tmp_path):
    ledger, record = job(tmp_path, {"max_iterations": 4, "no_progress_seconds": 1, "wall_seconds": 4})
    event_reader_closed = asyncio.Event()

    class SlowWorker(ClosableWorker):
        async def events(self):
            try:
                await asyncio.sleep(10)
                yield {"kind": "completed"}
            finally:
                event_reader_closed.set()

    worker = SlowWorker([])
    final = await PrimeSupervisor(ledger, lambda _job: worker, policy(record)).run(record["job_id"], "work")
    await asyncio.sleep(0)
    assert final["last_error"] == "no-progress limit"
    assert event_reader_closed.is_set() and worker.close_count == 1


@pytest.mark.asyncio
async def test_startup_is_inside_the_wall_clock_limit(tmp_path):
    ledger, record = job(tmp_path, {"max_iterations": 4, "no_progress_seconds": 4, "wall_seconds": 1})

    class SlowStart(ClosableWorker):
        async def start(self, **_kwargs):
            await asyncio.sleep(10)
            return {"session_id": "late", "trace_ref": "late"}

    worker = SlowStart([])
    final = await PrimeSupervisor(ledger, lambda _job: worker, policy(record)).run(record["job_id"], "work")
    assert final["status"] == "failed" and final["last_error"] == "wall-clock limit"
    assert worker.close_count == 1


@pytest.mark.asyncio
async def test_unproven_completion_cleanup_fails_job_and_retries_teardown(tmp_path):
    ledger, record = job(tmp_path)

    class RetryClose(ClosableWorker):
        async def close(self):
            self.close_count += 1
            if self.close_count == 1:
                raise OSError("transient teardown failure")
            self.closed = True

    worker = RetryClose([{"kind": "completed", "summary": "done"}])
    final = await PrimeSupervisor(ledger, lambda _job: worker, policy(record)).run(record["job_id"], "work")
    assert final["status"] == "failed" and "cleanup failed" in final["last_error"]
    assert worker.closed and worker.close_count == 2
