from pathlib import Path

import pytest

from prime_bridge.job_ledger import JobLedger, LedgerError
from prime_bridge.session_control import PrimeSessionControl, SessionControlError


RUNTIME = {
    "source_commit": "a" * 40,
    "image_digest": "sha256:" + "b" * 64,
    "sandbox": "rootless-podman",
    "command_fingerprint": "c" * 64,
}
RUNTIME["fingerprint"] = JobLedger.runtime_fingerprint(RUNTIME)


def make_job(tmp_path: Path, *, profile: str = "workspace", session_id: str = "hermes-one"):
    root = tmp_path / "root"
    root.mkdir(parents=True)
    ledger = JobLedger(tmp_path / "jobs.sqlite3", [root])
    route = {
        "provider": "ollama-cloud", "model": "glm-5.2:cloud",
        "api": "openai-completions", "endpoint": "http://relay",
    }
    route["fingerprint"] = JobLedger.route_fingerprint(route)
    job = ledger.create(
        owner="hermes-session", profile=profile, project="__PRIVATE_TOKEN_02__", session_id=session_id,
        workspace=root, route=route, runtime=RUNTIME,
        limits={"max_iterations": 4, "no_progress_seconds": 30, "wall_seconds": 60},
    )
    return ledger, job


def test_route_preserves_native_and_requires_explicit_handoff(tmp_path):
    ledger, _job = make_job(tmp_path)
    prime = PrimeSessionControl(ledger, profile="workspace", mode="prime-default", enabled=True).route()
    assert prime["recommended_engine"] == "prime"
    assert prime["native_available"] is True and prime["existing_sessions_unchanged"] is True
    native = PrimeSessionControl(ledger, profile="telegram", mode="native-only", enabled=True).route()
    assert native["recommended_engine"] == "native" and native["prime_available"] is False


def test_incremental_events_are_monotonic_bounded_and_compatible(tmp_path):
    ledger, job = make_job(tmp_path)
    ledger.note(job["job_id"], "session_handoff", {"hermes_session_id": job["session_id"]})
    ledger.note(job["job_id"], "session_detached", {"hermes_session_id": job["session_id"]})
    first = ledger.events(job["job_id"], limit=2)
    second = ledger.events(job["job_id"], after_event_id=first[-1]["event_id"])
    assert [item["event_id"] for item in first + second] == sorted(item["event_id"] for item in first + second)
    assert {item["kind"] for item in ledger.events(job["job_id"])} >= {"created", "session_handoff", "session_detached"}
    with pytest.raises(LedgerError):
        ledger.events(job["job_id"], after_event_id=-1)
    with pytest.raises(LedgerError):
        ledger.events(job["job_id"], limit=201)


def test_detach_reattach_steer_interrupt_and_scope(tmp_path):
    ledger, job = make_job(tmp_path)
    control = PrimeSessionControl(ledger, profile="workspace", mode="prime-default", enabled=True)
    ledger.note(job["job_id"], "session_handoff", {"hermes_session_id": job["session_id"]})
    ledger.acquire(job["job_id"], "supervisor", 90)
    ledger.transition(job["job_id"], "starting")
    ledger.transition(job["job_id"], "running", prime_session_id="prime-one")
    assert control.detach(job["job_id"], session_id="hermes-one")["attachment"] == "detached"
    assert ledger.get(job["job_id"])["status"] == "running"
    assert control.reattach(job["job_id"], session_id="hermes-one")["attachment"] == "attached"
    assert control.steer(job["job_id"], session_id="hermes-one", text="focus tests")["status"] == "queued"
    assert control.interrupt(job["job_id"], session_id="hermes-one")["status"] == "queued"
    with pytest.raises(SessionControlError, match="identity mismatch"):
        control.detach(job["job_id"], session_id="other")
    with pytest.raises(SessionControlError, match="not owned"):
        PrimeSessionControl(ledger, profile="home", mode="prime-available", enabled=True).status(job["job_id"])


def test_duplicate_active_handoff_is_rejected_and_public_status_is_sanitized(tmp_path):
    ledger, job = make_job(tmp_path)
    control = PrimeSessionControl(ledger, profile="workspace", mode="prime-default", enabled=True)
    with pytest.raises(SessionControlError, match="already has"):
        control.require_no_active(project="__PRIVATE_TOKEN_02__", session_id="hermes-one")
    status = control.status(job["job_id"])
    assert "workspace" not in status["job"] and "trace_ref" not in status["job"]
    assert status["job"]["provider"] == "ollama-cloud"


def test_attachment_state_reads_beyond_one_event_page(tmp_path):
    ledger, job = make_job(tmp_path)
    control = PrimeSessionControl(ledger, profile="workspace", mode="prime-default", enabled=True)
    ledger.note(job["job_id"], "session_handoff", {"hermes_session_id": "hermes-one"})
    for index in range(205):
        ledger.note(job["job_id"], "heartbeat", {"index": index})
    ledger.note(job["job_id"], "session_detached", {"hermes_session_id": "hermes-one"})
    assert control.status(job["job_id"], session_id="hermes-one")["attachment"] == "detached"
