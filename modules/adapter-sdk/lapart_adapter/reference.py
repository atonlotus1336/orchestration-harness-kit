"""Provider-free JSON reference adapter for authoring and conformance tests."""

from __future__ import annotations

from typing import Any

from .adapter import AdapterRequest, HarnessAdapter


class JsonReferenceAdapter(HarnessAdapter):
    def initialize(self, config_reference: str, data_reference: str) -> dict[str, Any]:
        return {"status": "ready", "config_reference": config_reference, "data_reference": data_reference}

    def shutdown(self, reason: str) -> dict[str, Any]:
        return {"status": "closed", "reason": reason}

    def translate_request(self, local_event: Any) -> AdapterRequest:
        if not isinstance(local_event, dict) or set(local_event) != {
            "operation",
            "payload",
            "session_id",
            "session_state",
        }:
            raise ValueError("reference event keys are not closed")
        return AdapterRequest(
            operation=local_event["operation"],
            payload=dict(local_event["payload"]),
            session_id=local_event["session_id"],
            session_state=local_event["session_state"],
        )

    def translate_response(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": envelope["kind"],
            "operation": envelope["operation"],
            "session": dict(envelope["session"]),
            "payload": dict(envelope["payload"]),
            "provider_accounting": dict(envelope["provider_accounting"]),
            "context_attribution": envelope.get("context_attribution"),
            "error": envelope.get("error"),
        }

    def readiness_signal(self) -> dict[str, Any]:
        return {"status": "ready", "provider_calls": 0}

    def rollback_signal(self) -> dict[str, Any]:
        return {"status": "inactive", "rollback_reference": None}
