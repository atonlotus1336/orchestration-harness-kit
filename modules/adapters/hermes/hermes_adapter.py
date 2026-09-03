"""Translation-only Hermes OS adapter for the universal orchestration bridge."""

from __future__ import annotations

from typing import Any

from lapart_adapter import AdapterRequest, HarnessAdapter


SURFACES = {"home", "workspace", "nucbox-telegram", "vps-telegram"}


class HermesOSAdapter(HarnessAdapter):
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
        expected = {"operation", "payload", "conversation", "state", "surface"}
        if not isinstance(local_event, dict) or set(local_event) != expected:
            raise ValueError("Hermes event keys are not closed")
        if local_event["surface"] not in SURFACES:
            raise ValueError("Hermes surface is not qualified")
        return AdapterRequest(
            operation=local_event["operation"],
            payload=dict(local_event["payload"]),
            session_id=local_event["conversation"],
            session_state=local_event["state"],
        )

    def translate_response(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "event": "orchestration.response",
            "operation": envelope["operation"],
            "conversation": dict(envelope["session"]),
            "status": "ok" if envelope["kind"] == "response" else "error",
            "result": dict(envelope["payload"]),
            "provider_accounting": dict(envelope["provider_accounting"]),
            "context_attribution": envelope.get("context_attribution"),
            "error": envelope.get("error"),
        }

    def readiness_signal(self) -> dict[str, Any]:
        return {"status": "ready" if self._ready else "inactive", "provider_calls": 0}

    def rollback_signal(self) -> dict[str, Any]:
        return {"status": "runtime-unchanged", "rollback_reference": "no-live-mutation"}
