"""Profile-owned Hermes control contract for Prime-backed sessions."""

from __future__ import annotations

from typing import Any

from prime_bridge.job_ledger import TERMINAL, JobLedger, LedgerError


class SessionControlError(RuntimeError):
    pass


class PrimeSessionControl:
    """Expose safe session operations over the existing durable job ledger."""

    def __init__(self, ledger: JobLedger, *, profile: str, mode: str, enabled: bool):
        self.ledger = ledger
        self.profile = profile
        self.mode = mode
        self.enabled = enabled

    def route(self) -> dict[str, Any]:
        available = self.enabled and self.mode != "native-only"
        return {
            "profile": self.profile,
            "mode": self.mode,
            "recommended_engine": "prime" if available and self.mode == "prime-default" else "native",
            "prime_available": available,
            "native_available": True,
            "native_fallback": True,
            "explicit_handoff_required": True,
            "activation": "restart-boundary",
            "existing_sessions_unchanged": True,
        }

    def active_for(self, *, project: str, session_id: str) -> list[dict[str, Any]]:
        return [
            job for job in self.ledger.list(profile=self.profile, nonterminal=True)
            if job["project"] == project and job["session_id"] == session_id
        ]

    def require_no_active(self, *, project: str, session_id: str) -> None:
        if self.active_for(project=project, session_id=session_id):
            raise SessionControlError("Hermes session already has an active Prime handoff")

    def owned(self, job_id: str, *, session_id: str | None = None) -> dict[str, Any]:
        try:
            job = self.ledger.get(job_id)
        except LedgerError as exc:
            raise SessionControlError("unknown Prime session job") from exc
        if job["profile"] != self.profile:
            raise SessionControlError("Prime session job is not owned by the current profile")
        if session_id is not None and job["session_id"] != session_id:
            raise SessionControlError("Hermes session identity mismatch")
        return job

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: job.get(key) for key in (
                "job_id", "profile", "project", "session_id", "status", "created_at",
                "updated_at", "started_at", "finished_at", "iteration",
                "prime_session_id", "result_summary", "last_error", "recovery",
            )
        } | {
            "provider": job["route"]["provider"],
            "model": job["route"]["model"],
        }

    def _attachment(self, job_id: str) -> str:
        state = "attached"
        cursor = 0
        while True:
            events = self.ledger.events(job_id, after_event_id=cursor)
            for event in events:
                if event["kind"] == "session_detached":
                    state = "detached"
                elif event["kind"] in {"session_handoff", "session_reattached"}:
                    state = "attached"
            if not events:
                break
            cursor = events[-1]["event_id"]
            if len(events) < 200:
                break
        return state

    def status(self, job_id: str, *, session_id: str | None = None) -> dict[str, Any]:
        job = self.owned(job_id, session_id=session_id)
        return {"job": self._public_job(job), "attachment": self._attachment(job_id)}

    def events(
        self, job_id: str, *, session_id: str | None = None,
        after_event_id: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        self.owned(job_id, session_id=session_id)
        events = self.ledger.events(job_id, after_event_id=after_event_id, limit=limit)
        return {
            "job_id": job_id,
            "events": events,
            "next_cursor": events[-1]["event_id"] if events else after_event_id,
            "has_more": len(events) == limit,
        }

    def detach(self, job_id: str, *, session_id: str) -> dict[str, Any]:
        self.owned(job_id, session_id=session_id)
        if self._attachment(job_id) == "detached":
            return self.status(job_id, session_id=session_id)
        self.ledger.note(job_id, "session_detached", {"hermes_session_id": session_id})
        return self.status(job_id, session_id=session_id)

    def reattach(self, job_id: str, *, session_id: str) -> dict[str, Any]:
        self.owned(job_id, session_id=session_id)
        if self._attachment(job_id) == "attached":
            return self.status(job_id, session_id=session_id)
        self.ledger.note(job_id, "session_reattached", {"hermes_session_id": session_id})
        return self.status(job_id, session_id=session_id)

    def steer(self, job_id: str, *, session_id: str, text: str) -> dict[str, str]:
        self.owned(job_id, session_id=session_id)
        try:
            command_id = self.ledger.enqueue_command(job_id, "steer", text)
        except LedgerError as exc:
            raise SessionControlError("Prime session cannot accept steering") from exc
        return {"job_id": job_id, "command_id": command_id, "status": "queued"}

    def interrupt(self, job_id: str, *, session_id: str) -> dict[str, Any]:
        job = self.owned(job_id, session_id=session_id)
        if job["status"] in TERMINAL:
            return {"job_id": job_id, "status": job["status"], "already_terminal": True}
        try:
            if job["status"] == "created":
                cancelled = self.ledger.transition(job_id, "cancelled")
                return {"job_id": job_id, "status": cancelled["status"]}
            command_id = self.ledger.enqueue_command(job_id, "cancel")
        except LedgerError as exc:
            raise SessionControlError("Prime session cannot be interrupted") from exc
        return {"job_id": job_id, "command_id": command_id, "status": "queued"}
