"""Restart reconciliation between the Hermes ledger and Prime daemon inventory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prime_bridge.job_ledger import JobLedger, LedgerError


async def reconcile(
    ledger: JobLedger,
    inventory: list[dict[str, Any]],
    *,
    attach: Any,
    abort: Any,
    now: datetime | None = None,
    enabled: bool = True,
) -> dict[str, list[str]]:
    """Reattach exact matches and fail closed for every ambiguous worker."""
    instant = now or datetime.now(timezone.utc)
    active = {
        job["job_id"]: job for job in ledger.list(nonterminal=True)
        if job["status"] in {"starting", "running", "stopping"}
    }
    workers: dict[str, list[dict[str, Any]]] = {}
    invalid_workers = []
    for worker in inventory:
        job_id = worker.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            invalid_workers.append(worker)
        else:
            workers.setdefault(job_id, []).append(worker)
    result: dict[str, list[str]] = {"reattached": [], "orphaned": [], "cancelled": [], "aborted": []}

    for worker in invalid_workers:
        await abort(worker)
        result["aborted"].append("<unknown>")

    for job_id, job in active.items():
        candidates = workers.pop(job_id, [])
        worker = candidates[0] if len(candidates) == 1 else None
        prime_session_id = job.get("prime_session_id")
        if job["status"] == "stopping":
            for candidate in candidates:
                await abort(candidate)
                result["aborted"].append(job_id)
            ledger.transition(job_id, "cancelled", recovery={"state": "cancelled", "reason": "restart completed pending cancellation"})
            result["cancelled"].append(job_id)
            continue
        exact = (
            worker is not None
            and isinstance(prime_session_id, str)
            and bool(prime_session_id)
            and worker.get("session_id") == prime_session_id
            and worker.get("runtime_fingerprint") == job["runtime"]["fingerprint"]
        )
        lease_live = False
        if exact:
            try:
                lease_live = not ledger.lease_expired(job_id, now=instant)
            except (KeyError, LedgerError, TypeError, ValueError):
                lease_live = False
        if enabled and exact and lease_live:
            try:
                await attach(worker)
                ledger.recover(job_id, {"state": "reattached", "session_id": worker["session_id"]})
            except Exception:  # noqa: BLE001 - an untrusted attach callback must always fail closed
                await abort(worker)
                result["aborted"].append(job_id)
                candidates = []
            else:
                result["reattached"].append(job_id)
                continue
        for candidate in candidates:
            await abort(candidate)
            result["aborted"].append(job_id)
        ledger.transition(job_id, "orphaned", recovery={"state": "orphaned", "reason": "disabled, expired, missing, or mismatched worker"})
        result["orphaned"].append(job_id)

    for job_id, candidates in workers.items():
        for worker in candidates:
            await abort(worker)
            result["aborted"].append(job_id)
    return result
