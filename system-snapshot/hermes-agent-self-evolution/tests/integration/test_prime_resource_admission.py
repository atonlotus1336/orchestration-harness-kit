from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prime_bridge.admission import AdmissionController
from prime_bridge.job_ledger import JobLedger
from prime_bridge.rollout_policy import RolloutPolicy
from prime_bridge.supervisor import PrimePolicy, PrimeSupervisor, SupervisorError


ROOT = Path(__file__).resolve().parents[2]
LIMITS = RolloutPolicy.load(ROOT / "config/prime-rollout-policy.json").admission
RUNTIME = {"source_commit": "a" * 40, "image_digest": "sha256:" + "b" * 64, "sandbox": "rootless-podman", "command_fingerprint": "c" * 64}
RUNTIME["fingerprint"] = JobLedger.runtime_fingerprint(RUNTIME)


def snapshot(cpu=10, memory=20, *, status="VERIFIED", sampled_at=None):
    return {
        "status": status,
        "cpu_percent": cpu if status == "VERIFIED" else None,
        "memory_percent": memory if status == "VERIFIED" else None,
        "sampled_at": (sampled_at or datetime.now(timezone.utc)).isoformat(),
        "source": "controlled",
    }


def records(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir(parents=True)
    ledger = JobLedger(tmp_path / "jobs.sqlite3", [root])
    route = {"provider": "ollama-cloud", "model": "glm", "api": "openai-completions", "endpoint": "http://relay"}
    route["fingerprint"] = JobLedger.route_fingerprint(route)
    values = []
    for profile, session in (("home", "one"), ("workspace", "two"), ("home", "three")):
        values.append(ledger.create(owner="test", profile=profile, project="proj", session_id=session, workspace=root, route=route, runtime=RUNTIME, limits={"max_iterations": 2, "no_progress_seconds": 20, "wall_seconds": 30}))
    return ledger, values


def policy(job):
    return PrimePolicy(
        profile_modes={"home": "prime-available", "workspace": "prime-default"}, max_active=16,
        allowed_route_fingerprints=frozenset({job["route"]["fingerprint"]}),
        allowed_runtime_fingerprints=frozenset({job["runtime"]["fingerprint"]}),
    )


def test_fresh_low_pressure_admits(tmp_path):
    ledger, jobs = records(tmp_path)
    controller = AdmissionController(LIMITS, lambda: snapshot())
    supervisor = PrimeSupervisor(ledger, lambda _job: None, policy(jobs[0]), admission_controller=controller)
    supervisor._admit(jobs[0])
    assert controller.last_decision["status"] == "ADMIT"
    assert controller.last_decision["native_reserve_percent"] == 20


@pytest.mark.parametrize("failure", ["unknown", "stale", "cpu", "memory", "profile", "global"])
def test_every_pressure_and_capacity_boundary_rejects_before_worker(tmp_path, failure):
    ledger, jobs = records(tmp_path)
    sample = snapshot()
    if failure == "unknown":
        sample = snapshot(status="UNVERIFIED")
    if failure == "stale":
        sample = snapshot(sampled_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    if failure == "cpu":
        sample = snapshot(cpu=95)
    if failure == "memory":
        sample = snapshot(memory=95)
    if failure in {"profile", "global"}:
        active = jobs[0]
        ledger.acquire(active["job_id"], "loop", 60)
        ledger.transition(active["job_id"], "starting")
        if failure == "global":
            other = jobs[1]
            ledger.acquire(other["job_id"], "loop2", 60)
            ledger.transition(other["job_id"], "starting")
    controller = AdmissionController(LIMITS, lambda: sample)
    target = jobs[2] if failure == "profile" else jobs[1] if failure == "global" else jobs[0]
    supervisor = PrimeSupervisor(ledger, lambda _job: (_ for _ in ()).throw(AssertionError("worker created")), policy(target), admission_controller=controller)
    with pytest.raises(SupervisorError, match="resource admission"):
        supervisor.submit(target["job_id"], "work")
    assert controller.last_decision["status"] == "REJECT" and controller.last_decision["native_fallback"] is True
