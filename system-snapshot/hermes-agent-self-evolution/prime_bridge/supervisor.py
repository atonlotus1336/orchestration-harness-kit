"""Hermes-owned policy and lifecycle boundary around Prime's native daemon/RPC."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from prime_bridge.job_ledger import TERMINAL, JobLedger, LedgerError, validate_safe


class PrimeWorker(Protocol):
    runtime_fingerprint: str
    async def start(self, *, prompt: str, workspace: str, route: dict[str, str]) -> dict[str, str]: ...
    def events(self) -> AsyncIterator[dict[str, Any]]: ...
    async def command(self, *, command_id: str, kind: str, text: str | None = None) -> None: ...


class SupervisorError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrimePolicy:
    enabled: bool = True
    profile_modes: dict[str, str] = field(default_factory=dict)
    max_active: int = 2
    allowed_route_fingerprints: frozenset[str] = frozenset()
    allowed_runtime_fingerprints: frozenset[str] = frozenset()

    def permits(self, profile: str) -> bool:
        mode = self.profile_modes.get(profile, "native-only")
        return self.enabled and mode in {"prime-enabled", "prime-available", "prime-default"}


class PrimeSupervisor:
    """Runs bounded Prime work independently of a UI/client connection."""

    def __init__(self, ledger: JobLedger, worker_factory: Any, policy: PrimePolicy | None = None, usage_provider: Any | None = None, admission_controller: Any | None = None):
        self.ledger = ledger
        self.worker_factory = worker_factory
        self.policy = policy or PrimePolicy()
        self._workers: dict[str, PrimeWorker] = {}
        self._tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._pending: set[str] = set()
        self.usage_provider = usage_provider or (lambda _job_id: {"status": "not-sampled"})
        self.admission_controller = admission_controller

    def _admit(self, job: dict[str, Any]) -> None:
        if not self.policy.permits(job["profile"]):
            raise SupervisorError("Prime is disabled for this profile")
        if job["route"]["fingerprint"] not in self.policy.allowed_route_fingerprints:
            raise SupervisorError("Prime route is not pinned by Hermes authority")
        if job["runtime"]["fingerprint"] not in self.policy.allowed_runtime_fingerprints:
            raise SupervisorError("Prime runtime is not pinned by Hermes authority")
        active = self.ledger.list(nonterminal=True)
        occupied = sum(item["status"] in {"starting", "running", "stopping"} for item in active) + len(self._pending)
        if occupied >= self.policy.max_active:
            raise SupervisorError("Prime admission limit reached")
        if self.admission_controller is not None:
            decision = self.admission_controller.assess(job, active)
            if decision["status"] != "ADMIT":
                raise SupervisorError(f"Prime resource admission rejected: {','.join(decision['reasons'])}")

    def submit(self, job_id: str, prompt: str, *, owner: str = "hermes-supervisor") -> asyncio.Task[dict[str, Any]]:
        if job_id in self._tasks and not self._tasks[job_id].done():
            raise SupervisorError("job already supervised")
        job = self.ledger.get(job_id)
        self._admit(job)
        self._prompt(prompt)
        task = asyncio.create_task(self.run(job_id, prompt, owner=owner))
        self._pending.add(job_id)
        task.add_done_callback(lambda _task: self._pending.discard(job_id))
        self._tasks[job_id] = task
        return task

    async def run(self, job_id: str, prompt: str, *, owner: str = "hermes-supervisor") -> dict[str, Any]:
        job = self.ledger.get(job_id)
        reserved = job_id in self._pending
        self._pending.discard(job_id)
        if not reserved:
            self._admit(job)
            self._prompt(prompt)
        self.ledger.acquire(job_id, owner, max(job["limits"]["wall_seconds"] + 10, 30))
        self.ledger.transition(job_id, "starting")
        started = datetime.now(timezone.utc)
        worker: PrimeWorker | None = None
        event_task: asyncio.Task[dict[str, Any]] | None = None
        worker_closed = False
        try:
            worker = self.worker_factory(job)
            self._workers[job_id] = worker
            if getattr(worker, "runtime_fingerprint", None) != job["runtime"]["fingerprint"]:
                raise SupervisorError("worker runtime does not match the admitted authority")
            try:
                opened = await asyncio.wait_for(
                    worker.start(prompt=prompt, workspace=job["workspace"], route=job["route"]),
                    timeout=job["limits"]["wall_seconds"],
                )
            except asyncio.TimeoutError:
                return self.ledger.transition(job_id, "failed", last_error="wall-clock limit")
            self.ledger.transition(
                job_id, "running", prime_session_id=opened.get("session_id"), trace_ref=opened.get("trace_ref")
            )
            iterator = worker.events().__aiter__()
            pending_event: dict[str, Any] | None = None
            while True:
                current = self.ledger.get(job_id)
                if current["status"] in TERMINAL:
                    return current
                for command in self.ledger.pending_commands(job_id):
                    self.ledger.claim_command(job_id, command["command_id"])
                    if command["kind"] == "steer":
                        await worker.command(command_id=command["command_id"], kind="steer", text=command["text"])
                        self.ledger.acknowledge_command(job_id, command["command_id"])
                    else:
                        if current["status"] == "running":
                            self.ledger.transition(job_id, "stopping")
                        await self._abort(job_id, worker, "operator cancellation")
                        self.ledger.acknowledge_command(job_id, command["command_id"])
                        return self.ledger.transition(job_id, "cancelled")
                if pending_event is None:
                    remaining = current["limits"]["wall_seconds"] - (datetime.now(timezone.utc) - started).total_seconds()
                    no_progress = current["limits"]["no_progress_seconds"] - (
                        datetime.now(timezone.utc) - datetime.fromisoformat(current["last_progress_at"])
                    ).total_seconds()
                    timeout = min(remaining, no_progress)
                    if timeout <= 0:
                        reason = "wall-clock limit" if remaining <= 0 else "no-progress limit"
                        await self._abort(job_id, worker, reason)
                        return self.ledger.transition(job_id, "failed", last_error=reason)
                    if event_task is None:
                        event_task = asyncio.create_task(iterator.__anext__())
                    try:
                        done, _pending = await asyncio.wait({event_task}, timeout=min(timeout, 0.1))
                        if not done:
                            continue
                        pending_event = event_task.result()
                        event_task = None
                        continue
                    except StopAsyncIteration:
                        current = self.ledger.get(job_id)
                        return current if current["status"] in TERMINAL else self.ledger.transition(job_id, "failed", last_error="Prime event stream ended")
                event = pending_event
                pending_event = None
                current = self.ledger.get(job_id)
                if current["status"] in TERMINAL:
                    return current
                kind = event.get("kind")
                if kind == "progress":
                    current = self.ledger.progress(job_id, owner, trace_ref=event.get("trace_ref"))
                    if current["iteration"] >= current["limits"]["max_iterations"]:
                        await self._abort(job_id, worker, "iteration limit")
                        return self.ledger.transition(job_id, "failed", last_error="iteration limit")
                elif kind == "completed":
                    cleanup_error = await self._close_worker(worker)
                    worker_closed = cleanup_error is None
                    if cleanup_error:
                        return self.ledger.transition(job_id, "failed", last_error=cleanup_error)
                    summary = str(event.get("summary", "completed"))
                    if prompt:
                        summary = summary.replace(prompt, "[prompt-redacted]")
                    return self.ledger.transition(job_id, "completed", result_summary=summary)
                elif kind == "failed":
                    return self.ledger.transition(job_id, "failed", last_error=event.get("error", "Prime failed"))
        except Exception as exc:
            current = self.ledger.get(job_id)
            if current["status"] not in TERMINAL:
                return self.ledger.transition(job_id, "failed", last_error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._workers.pop(job_id, None)
            if event_task is not None and not event_task.done():
                event_task.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await event_task
            if worker is not None and not worker_closed:
                cleanup_error = await self._close_worker(worker)
                if cleanup_error:
                    current = self.ledger.get(job_id)
                    if current["status"] not in TERMINAL:
                        self.ledger.transition(job_id, "failed", last_error=cleanup_error)
                    else:
                        self.ledger.note(job_id, "cleanup_warning", {"error": cleanup_error})

    @staticmethod
    def _prompt(prompt: str) -> None:
        if not isinstance(prompt, str) or not prompt.strip():
            raise SupervisorError("Prime prompt is required")
        try:
            validate_safe(prompt)
        except LedgerError as exc:
            raise SupervisorError("Prime prompt failed the transient safety boundary") from exc

    @staticmethod
    async def _close_worker(worker: PrimeWorker) -> str | None:
        close = getattr(worker, "close", None)
        if close is None:
            return None
        try:
            await close()
            return None
        except Exception as exc:  # noqa: BLE001 - cleanup failures become durable job evidence
            return f"worker cleanup failed: {type(exc).__name__}"

    async def _abort(self, job_id: str, worker: PrimeWorker, reason: str) -> None:
        await worker.command(command_id=uuid.uuid4().hex, kind="abort", text=reason)
        self.ledger.note(job_id, "abort_sent", {"reason": reason})

    async def steer(self, job_id: str, text: str) -> str:
        if self.ledger.get(job_id)["status"] != "running":
            raise SupervisorError("job is not running")
        return self.ledger.enqueue_command(job_id, "steer", text)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.ledger.get(job_id)
        if job["status"] in TERMINAL:
            return job
        if job["status"] == "created":
            return self.ledger.transition(job_id, "cancelled")
        command_id = self.ledger.enqueue_command(job_id, "cancel")
        return {"command_id": command_id, "status": "queued"}

    def observe(self, job_id: str) -> dict[str, Any]:
        return {"job": self.ledger.get(job_id), "events": self.ledger.events(job_id)}

    def submit_omp(self, job_id: str, *, kind: str, record: dict[str, Any]) -> dict[str, Any]:
        """Create a durable child OMP job bound to a parent Prime job."""
        parent = self.ledger.get(job_id)
        if parent["status"] not in {"starting", "running"}:
            raise SupervisorError("OMP child requires an active Prime parent job")
        return self.ledger.create_child(parent_job_id=job_id, kind=kind, record=record)

    def omp_status(self, child_job_id: str) -> dict[str, Any]:
        return self.ledger.child(child_job_id)

    def omp_cancel(self, child_job_id: str) -> dict[str, Any]:
        child = self.ledger.child(child_job_id)
        if child["status"] in {"completed", "failed", "cancelled"}:
            return child
        return self.ledger.update_child(child_job_id, status="cancelled", record=child["record"])

    def omp_result(self, child_job_id: str) -> dict[str, Any]:
        child = self.ledger.child(child_job_id)
        if child["status"] not in {"completed", "failed", "cancelled"}:
            raise SupervisorError("OMP child has no terminal result yet")
        return child

    def health(self) -> dict[str, Any]:
        jobs = self.ledger.list()
        counts = {status: sum(job["status"] == status for job in jobs) for status in sorted({job["status"] for job in jobs})}
        active = []
        for job in jobs:
            if job["status"] not in {"starting", "running", "stopping"}: continue
            active.append({
                "job_id": job["job_id"], "profile": job["profile"], "project": job["project"],
                "status": job["status"], "iteration": job["iteration"], "last_error": job["last_error"],
                "provider": job["route"]["provider"], "model": job["route"]["model"],
                "prime_source": job["runtime"]["source_commit"], "image_digest": job["runtime"]["image_digest"],
                "resource_usage": self.usage_provider(job["job_id"]),
            })
        return {
            "status": "ready" if self.policy.enabled else "disabled",
            "bridge_version": "1.0", "prime_protocol": "v0.7.1-rpc",
            "metrics": {"jobs_total": len(jobs), "status_counts": counts, "active": len(active), "admission_limit": self.policy.max_active},
            "active_jobs": active,
            "resource_admission": (
                self.admission_controller.last_decision if self.admission_controller is not None
                else {"status": "NOT_CONFIGURED"}
            ),
        }
