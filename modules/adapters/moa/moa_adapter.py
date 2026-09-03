"""Translation-only MOA adapter for the universal orchestration bridge."""

from __future__ import annotations

from typing import Any

from lapart_adapter import AdapterRequest, HarnessAdapter


PHASES = {"propose", "critique", "aggregate", "event", "lifecycle"}


class MoaAdapter(HarnessAdapter):
    def __init__(self, manifest):
        super().__init__(manifest)
        self._ready = False

    def initialize(self, config_reference: str, data_reference: str) -> dict[str, Any]:
        self._ready = True
        return {"status": "ready", "runtime_changed": False, "provider_calls": 0}

    def shutdown(self, reason: str) -> dict[str, Any]:
        self._ready = False
        return {"status": "closed", "reason": reason, "runtime_changed": False}

    def translate_request(self, local_event: Any) -> AdapterRequest:
        if not isinstance(local_event, dict) or set(local_event) != {"phase", "operation", "content", "session"}:
            raise ValueError("MOA event keys are not closed")
        session = local_event["session"]
        if local_event["phase"] not in PHASES or not isinstance(session, dict) or set(session) != {"id", "state"}:
            raise ValueError("MOA phase or session is not supported")
        return AdapterRequest(
            operation=local_event["operation"], payload=dict(local_event["content"]),
            session_id=session["id"], session_state=session["state"],
        )

    def translate_response(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "phase": "lapart_result", "operation": envelope["operation"],
            "session": dict(envelope["session"]), "content": dict(envelope["payload"]),
            "provider_accounting": dict(envelope["provider_accounting"]),
            "context_attribution": envelope.get("context_attribution"), "error": envelope.get("error"),
        }

    def readiness_signal(self) -> dict[str, Any]:
        return {"status": "ready" if self._ready else "inactive", "provider_calls": 0}

    def rollback_signal(self) -> dict[str, Any]:
        return {"status": "runtime-unchanged", "rollback_reference": "no-live-mutation"}
