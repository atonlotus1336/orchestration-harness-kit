from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prime_bridge.job_ledger import JobLedger
from prime_bridge.recovery import reconcile


def running(tmp_path: Path, name: str, *, expired=False):
    root = tmp_path / "root"; root.mkdir(parents=True, exist_ok=True)
    ledger = JobLedger(tmp_path / "jobs.sqlite3", [root])
    public = {"provider": "hermes-ollama", "model": "glm-5.2:cloud", "api": "openai-completions", "endpoint": "http://relay"}
    runtime = {"source_commit": "a" * 40, "image_digest": "sha256:" + "b" * 64, "sandbox": "rootless-podman", "command_fingerprint": "c" * 64}
    record = ledger.create(owner="owner", profile="home", project="proj", session_id=name, workspace=root, route={**public, "fingerprint": JobLedger.route_fingerprint(public)}, runtime={**runtime, "fingerprint": JobLedger.runtime_fingerprint(runtime)}, limits={"max_iterations": 4, "no_progress_seconds": 2, "wall_seconds": 4})
    ledger.acquire(record["job_id"], "supervisor", 30, now=datetime.now(timezone.utc) - (timedelta(seconds=60) if expired else timedelta()))
    ledger.transition(record["job_id"], "starting")
    return ledger, ledger.transition(record["job_id"], "running", prime_session_id=f"prime-{name}")


@pytest.mark.asyncio
async def test_exact_worker_reattaches_idempotently(tmp_path):
    ledger, record = running(tmp_path, "one")
    attached, aborted = [], []
    worker = {"job_id": record["job_id"], "session_id": "prime-one", "runtime_fingerprint": record["runtime"]["fingerprint"]}
    first = await reconcile(ledger, [worker], attach=lambda value: append(attached, value), abort=lambda value: append(aborted, value))
    second = await reconcile(ledger, [worker], attach=lambda value: append(attached, value), abort=lambda value: append(aborted, value))
    assert first["reattached"] == [record["job_id"]] == second["reattached"]
    assert ledger.get(record["job_id"])["recovery"]["state"] == "reattached"


async def append(target, value):
    target.append(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "mismatch", "expired", "disabled"])
async def test_uncertain_worker_fails_closed(tmp_path, mode):
    ledger, record = running(tmp_path, "one", expired=mode == "expired")
    inventory = [] if mode == "missing" else [{"job_id": record["job_id"], "session_id": "wrong" if mode == "mismatch" else "prime-one", "runtime_fingerprint": record["runtime"]["fingerprint"]}]
    aborted = []
    result = await reconcile(ledger, inventory, attach=lambda value: append([], value), abort=lambda value: append(aborted, value), enabled=mode != "disabled")
    assert record["job_id"] in result["orphaned"]
    assert ledger.get(record["job_id"])["status"] == "orphaned"


@pytest.mark.asyncio
async def test_unknown_worker_is_aborted(tmp_path):
    ledger, _record = running(tmp_path, "one")
    unknown = {"job_id": "f" * 32, "session_id": "prime-x"}
    aborted = []
    result = await reconcile(ledger, [unknown], attach=lambda value: append([], value), abort=lambda value: append(aborted, value))
    assert unknown in aborted and unknown["job_id"] in result["aborted"]


@pytest.mark.asyncio
async def test_duplicate_workers_are_all_aborted_and_job_is_orphaned(tmp_path):
    ledger, record = running(tmp_path, "one")
    workers = [
        {"job_id": record["job_id"], "session_id": "prime-one", "runtime_fingerprint": record["runtime"]["fingerprint"], "worker": "a"},
        {"job_id": record["job_id"], "session_id": "prime-one", "runtime_fingerprint": record["runtime"]["fingerprint"], "worker": "b"},
    ]
    attached, aborted = [], []
    result = await reconcile(ledger, workers, attach=lambda value: append(attached, value), abort=lambda value: append(aborted, value))
    assert attached == [] and aborted == workers
    assert result["orphaned"] == [record["job_id"]]


@pytest.mark.asyncio
async def test_empty_session_cannot_be_an_exact_match(tmp_path):
    ledger, record = running(tmp_path, "one")
    with ledger._connect() as db:
        value = __import__("json").loads(db.execute("SELECT record_json FROM jobs WHERE job_id=?", (record["job_id"],)).fetchone()[0])
        value["prime_session_id"] = None
        db.execute("UPDATE jobs SET record_json=? WHERE job_id=?", (__import__("json").dumps(value, sort_keys=True, separators=(",", ":")), record["job_id"]))
    worker = {"job_id": record["job_id"], "session_id": None, "runtime_fingerprint": record["runtime"]["fingerprint"]}
    attached, aborted = [], []
    await reconcile(ledger, [worker], attach=lambda value: append(attached, value), abort=lambda value: append(aborted, value))
    assert attached == [] and aborted == [worker]
    assert ledger.get(record["job_id"])["status"] == "orphaned"


@pytest.mark.asyncio
async def test_runtime_mismatch_and_stopping_job_never_reattach(tmp_path):
    ledger, record = running(tmp_path, "one")
    wrong = {"job_id": record["job_id"], "session_id": "prime-one", "runtime_fingerprint": "f" * 64}
    attached, aborted = [], []
    await reconcile(ledger, [wrong], attach=lambda value: append(attached, value), abort=lambda value: append(aborted, value))
    assert attached == [] and aborted == [wrong] and ledger.get(record["job_id"])["status"] == "orphaned"

    ledger2, record2 = running(tmp_path / "stopping", "two")
    ledger2.transition(record2["job_id"], "stopping")
    exact = {"job_id": record2["job_id"], "session_id": "prime-two", "runtime_fingerprint": record2["runtime"]["fingerprint"]}
    attached2, aborted2 = [], []
    result = await reconcile(ledger2, [exact], attach=lambda value: append(attached2, value), abort=lambda value: append(aborted2, value))
    assert attached2 == [] and aborted2 == [exact]
    assert result["cancelled"] == [record2["job_id"]] and ledger2.get(record2["job_id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_attach_failure_aborts_and_orphans(tmp_path):
    ledger, record = running(tmp_path, "one")
    worker = {"job_id": record["job_id"], "session_id": "prime-one", "runtime_fingerprint": record["runtime"]["fingerprint"]}
    aborted = []

    async def broken_attach(_value):
        raise RuntimeError("attach failed")

    result = await reconcile(ledger, [worker], attach=broken_attach, abort=lambda value: append(aborted, value))
    assert aborted == [worker] and result["orphaned"] == [record["job_id"]]
    assert ledger.get(record["job_id"])["status"] == "orphaned"
