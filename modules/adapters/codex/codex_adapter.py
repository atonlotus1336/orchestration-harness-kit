"""Translation-only Codex adapter for the universal orchestration bridge."""

from __future__ import annotations

from typing import Any

from lapart_adapter import AdapterRequest, HarnessAdapter


NOTIFICATIONS = {"thread", "turn", "item", "event", "lifecycle"}


class CodexAdapter(HarnessAdapter):
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
        if not isinstance(local_event, dict) or set(local_event) != {"notification", "operation", "input", "thread"}:
            raise ValueError("Codex event keys are not closed")
        thread = local_event["thread"]
        if local_event["notification"] not in NOTIFICATIONS or not isinstance(thread, dict) or set(thread) != {"id", "state"}:
            raise ValueError("Codex notification or thread is not supported")
        return AdapterRequest(
            operation=local_event["operation"], payload=dict(local_event["input"]),
            session_id=thread["id"], session_state=thread["state"],
        )

    def translate_response(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "lapart.orchestration.result", "operation": envelope["operation"],
            "thread": dict(envelope["session"]), "result": dict(envelope["payload"]),
            "provider_accounting": dict(envelope["provider_accounting"]),
            "context_attribution": envelope.get("context_attribution"), "error": envelope.get("error"),
        }

    def readiness_signal(self) -> dict[str, Any]:
        return {"status": "ready" if self._ready else "inactive", "provider_calls": 0}

    def rollback_signal(self) -> dict[str, Any]:
        return {"status": "runtime-unchanged", "rollback_reference": "no-live-mutation"}
