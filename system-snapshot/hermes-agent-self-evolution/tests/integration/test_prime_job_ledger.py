from __future__ import annotations

import concurrent.futures
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from prime_bridge.job_ledger import JobLedger, LedgerError

ROOT = Path(__file__).resolve().parents[2]


def route() -> dict[str, str]:
    value = {"provider": "hermes-ollama", "model": "deepseek-v4-flash:cloud", "api": "openai-completions", "endpoint": "http://127.0.0.1:11434/v1"}
    return {**value, "fingerprint": JobLedger.route_fingerprint(value)}


def runtime() -> dict[str, str]:
    value = {"source_commit": "a" * 40, "image_digest": "sha256:" + "b" * 64, "sandbox": "rootless-podman", "command_fingerprint": "c" * 64}
    return {**value, "fingerprint": JobLedger.runtime_fingerprint(value)}


def make(tmp_path: Path) -> tuple[JobLedger, dict]:
    workspace = tmp_path / "work"; workspace.mkdir(parents=True)
    ledger = JobLedger(tmp_path / "jobs.sqlite3", [workspace])
    job = ledger.create(owner="user", profile="home", project="repo", session_id="session-1", workspace=workspace, route=route(), runtime=runtime(), limits={"max_iterations": 4, "no_progress_seconds": 30, "wall_seconds": 120})
    return ledger, job


def child_record(parent: dict, **extra) -> dict:
    capability = "e" * 64
    return {
        "admission": {
            "status": "ADMITTED",
            "parent_prime_job_id": parent["job_id"],
            "model_route_fingerprint": parent["route"]["fingerprint"],
            "omp_policy_fingerprint": "d" * 64,
            "project_root": parent["workspace"],
            "profile": parent["profile"],
            "shared_budget": True,
            "capability_digest": capability,
        },
        "capability_digest": capability,
        "requested_profile": parent["profile"],
        "route_fingerprint": parent["route"]["fingerprint"],
        "workspace_scope": [parent["workspace"]],
        "actual_lane": "T3_PRIME_OMP",
        "preset_digest": "f" * 64,
        "capability_request_id": "request-1",
        **extra,
    }


def authorize_child(ledger: JobLedger, parent: dict) -> None:
    ledger.note(parent["job_id"], "execution_lane", {"lane": "T3_PRIME_OMP"})
    ledger.note(parent["job_id"], "omp_policy_bound", {
        "policy_digest": "d" * 64, "preset_digest": "f" * 64, "profile": parent["profile"],
        "route_fingerprint": parent["route"]["fingerprint"],
        "workspace": parent["workspace"],
    })
    ledger.note(parent["job_id"], "capability_authorized", {
        "request_id": "request-1", "alias": "omp.worker.run", "capability_digest": "e" * 64,
    })
    ledger.note(parent["job_id"], "governed_execution_budget", {
        "status": "READY", "source": "prime-job-admission", "wall_seconds": 30,
        "tool_calls": 1, "provider_calls": 1, "repair_turns": 0,
    })


def test_record_is_schema_valid_durable_scoped_and_secret_free(tmp_path: Path) -> None:
    ledger, job = make(tmp_path)
    schema = __import__("json").loads((ROOT / "contracts/prime-job.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(job)
    assert JobLedger(ledger.path, [tmp_path / "work"]).get(job["job_id"]) == job
    with pytest.raises(LedgerError, match="outside"):
        ledger.create(owner="user", profile="home", project="repo", session_id="s2", workspace=tmp_path, route=route(), runtime=runtime(), limits={"max_iterations": 1, "no_progress_seconds": 1, "wall_seconds": 1})
    bad = route(); bad["model"] = "sk-" + "x" * 24
    with pytest.raises(LedgerError, match="secret"):
        ledger.create(owner="user", profile="home", project="repo", session_id="s3", workspace=tmp_path / "work", route=bad, runtime=runtime(), limits={"max_iterations": 1, "no_progress_seconds": 1, "wall_seconds": 1})


def test_route_fingerprint_must_match_route_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "work"; workspace.mkdir()
    ledger = JobLedger(tmp_path / "jobs.sqlite3", [workspace])
    forged = route(); forged["model"] = "different-model"
    with pytest.raises(LedgerError, match="route fingerprint mismatch"):
        ledger.create(owner="user", profile="home", project="repo", session_id="session-1", workspace=workspace, route=forged, runtime=runtime(), limits={"max_iterations": 1, "no_progress_seconds": 1, "wall_seconds": 1})


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:8642", "http://127.0.0.1:9119", "http://user:pass@model.invalid/v1"])
def test_route_rejects_hermes_control_ports_and_embedded_credentials(tmp_path: Path, endpoint: str) -> None:
    workspace = tmp_path / "work"; workspace.mkdir()
    ledger = JobLedger(tmp_path / "jobs.sqlite3", [workspace])
    value = route(); value["endpoint"] = endpoint; value["fingerprint"] = JobLedger.route_fingerprint(value)
    with pytest.raises(LedgerError, match="endpoint"):
        ledger.create(owner="user", profile="home", project="repo", session_id="session-1", workspace=workspace, route=value, runtime=runtime(), limits={"max_iterations": 1, "no_progress_seconds": 1, "wall_seconds": 1})


def test_exactly_one_loop_owner_and_expired_takeover(tmp_path: Path) -> None:
    ledger, job = make(tmp_path); job_id = job["job_id"]
    def claim(owner: str) -> bool:
        try: ledger.acquire(job_id, owner, 60); return True
        except LedgerError: return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(claim, ("owner-a", "owner-b"))) == 1
    current = ledger.get(job_id)["lease"]["owner"]
    other = "owner-b" if current == "owner-a" else "owner-a"
    with pytest.raises(LedgerError, match="already active"):
        ledger.acquire(job_id, other, 60)
    future = datetime.now(timezone.utc) + timedelta(seconds=61)
    assert ledger.acquire(job_id, other, 60, now=future)["lease"]["owner"] == other


def test_expired_or_terminal_lease_cannot_act(tmp_path: Path) -> None:
    ledger, job = make(tmp_path); job_id = job["job_id"]
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    ledger.acquire(job_id, "owner-a", 1, now=past)
    ledger.transition(job_id, "starting"); ledger.transition(job_id, "running")
    with pytest.raises(LedgerError, match="expired"):
        ledger.renew(job_id, "owner-a", 60)
    with pytest.raises(LedgerError, match="expired"):
        ledger.progress(job_id, "owner-a")
    ledger.transition(job_id, "completed")
    with pytest.raises(LedgerError, match="terminal"):
        ledger.acquire(job_id, "owner-b", 60)


def test_transitions_progress_events_and_terminal_immutability(tmp_path: Path) -> None:
    ledger, job = make(tmp_path); jid = job["job_id"]
    ledger.acquire(jid, "loop", 60)
    ledger.transition(jid, "starting")
    ledger.transition(jid, "running", prime_session_id="prime-1", trace_ref="prime://trace/1")
    progressed = ledger.progress(jid, "loop", prime_session_id="prime-1")
    assert progressed["iteration"] == 1
    complete = ledger.transition(jid, "completed", result_summary="done")
    assert complete["finished_at"] and complete["lease"]["owner"] is None
    with pytest.raises(LedgerError, match="invalid transition"):
        ledger.transition(jid, "failed")
    assert [event["kind"] for event in ledger.events(jid)] == ["created", "lease_acquired", "transition", "transition", "progress", "transition"]


def test_terminal_model_usage_is_closed_measured_and_durable(tmp_path: Path) -> None:
    ledger, job = make(tmp_path); jid = job["job_id"]
    ledger.transition(jid, "starting"); ledger.transition(jid, "running")
    usage = {
        "input_units": 8, "output_units": 3,
        "cache_read_units": 2, "cache_write_units": 1, "total_units": 14,
        "source": "prime_rpc_session_stats",
    }
    assert ledger.transition(jid, "completed", result_summary="done", model_usage=usage)["model_usage"] == usage
    assert JobLedger(ledger.path, [tmp_path / "work"]).get(jid)["model_usage"] == usage

    for malformed in (
        {**usage, "total_units": 12},
        {**usage, "source": "estimated"},
        {**usage, "input_units": -1},
        {**usage, "raw": "content"},
    ):
        ledger2, job2 = make(tmp_path / __import__("uuid").uuid4().hex)
        ledger2.transition(job2["job_id"], "starting"); ledger2.transition(job2["job_id"], "running")
        with pytest.raises(LedgerError, match="model usage"):
            ledger2.transition(job2["job_id"], "completed", model_usage=malformed)


def test_running_transition_starts_the_no_progress_clock(tmp_path: Path) -> None:
    ledger, job = make(tmp_path); jid = job["job_id"]
    with ledger._connect() as db:
        value = __import__("json").loads(db.execute("SELECT record_json FROM jobs WHERE job_id=?", (jid,)).fetchone()[0])
        value["last_progress_at"] = "2000-01-01T00:00:00+00:00"
        db.execute("UPDATE jobs SET record_json=? WHERE job_id=?", (__import__("json").dumps(value, sort_keys=True, separators=(",", ":")), jid))
    ledger.acquire(jid, "loop", 60); ledger.transition(jid, "starting")
    running = ledger.transition(jid, "running")
    assert datetime.fromisoformat(running["last_progress_at"]) > datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_terminal_parent_reconciles_running_child_atomically(tmp_path: Path) -> None:
    ledger, job = make(tmp_path); jid = job["job_id"]
    ledger.transition(jid, "starting"); ledger.transition(jid, "running")
    authorize_child(ledger, job)
    child = ledger.create_child(parent_job_id=jid, kind="omp_worker", record=child_record(job, route="codex"))
    ledger.update_child(child["child_job_id"], status="running", record=child["record"])

    ledger.transition(jid, "failed", last_error="exact root failure")

    reconciled = ledger.child(child["child_job_id"])
    assert reconciled["status"] == "failed"
    assert reconciled["record"]["reconciled"] is True
    assert reconciled["record"]["parent_status"] == "failed"
    assert reconciled["record"]["parent_error"] == "exact root failure"


def test_durable_command_inbox_is_exactly_once(tmp_path: Path) -> None:
    ledger, job = make(tmp_path); jid = job["job_id"]
    ledger.acquire(jid, "loop", 60); ledger.transition(jid, "starting"); ledger.transition(jid, "running")
    command_id = ledger.enqueue_command(jid, "steer", "focus tests")
    assert ledger.pending_commands(jid) == [{"command_id": command_id, "kind": "steer", "text": "focus tests", "created_at": ledger.pending_commands(jid)[0]["created_at"]}]
    ledger.claim_command(jid, command_id)
    assert ledger.pending_commands(jid) == []
    ledger.acknowledge_command(jid, command_id)
    assert ledger.pending_commands(jid) == []
    with pytest.raises(LedgerError, match="already-applied"):
        ledger.acknowledge_command(jid, command_id)
    with pytest.raises(LedgerError, match="steer text"):
        ledger.enqueue_command(jid, "steer", None)
    with pytest.raises(LedgerError, match="cancel text"):
        ledger.enqueue_command(jid, "cancel", "unexpected")


def test_claimed_command_is_never_replayed_after_crash(tmp_path: Path) -> None:
    ledger, job = make(tmp_path); jid = job["job_id"]
    ledger.acquire(jid, "loop", 60); ledger.transition(jid, "starting"); ledger.transition(jid, "running")
    command_id = ledger.enqueue_command(jid, "steer", "once")
    ledger.claim_command(jid, command_id)
    assert ledger.pending_commands(jid) == []
    with pytest.raises(LedgerError, match="already-claimed"):
        ledger.claim_command(jid, command_id)


def test_command_state_check_is_atomic_with_insert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger, job = make(tmp_path); jid = job["job_id"]
    ledger.acquire(jid, "loop", 60); ledger.transition(jid, "starting"); ledger.transition(jid, "running")
    read_complete = threading.Event(); allow_insert = threading.Event()
    original_get = ledger.get

    def racing_get(job_id: str) -> dict:
        value = original_get(job_id)
        if threading.current_thread().name.startswith("enqueue-thread"):
            read_complete.set(); allow_insert.wait(timeout=5)
        return value

    monkeypatch.setattr(ledger, "get", racing_get)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="enqueue-thread") as pool:
        future = pool.submit(ledger.enqueue_command, jid, "steer", "too late")
        assert read_complete.wait(timeout=5)
        ledger.transition(jid, "completed")
        allow_insert.set()
        with pytest.raises(LedgerError, match="terminal"):
            future.result(timeout=5)
