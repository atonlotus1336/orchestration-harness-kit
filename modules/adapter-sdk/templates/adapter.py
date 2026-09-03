"""Copyable translation-only harness adapter template."""

from __future__ import annotations

from typing import Any

from lapart_adapter import AdapterRequest, HarnessAdapter


class ExampleHarnessAdapter(HarnessAdapter):
    def initialize(self, config_reference: str, data_reference: str) -> dict[str, Any]:
        return {"status": "ready", "config_reference": config_reference, "data_reference": data_reference}

    def shutdown(self, reason: str) -> dict[str, Any]:
        return {"status": "closed", "reason": reason}

    def translate_request(self, local_event: Any) -> AdapterRequest:
        """Map the harness event to one shared operation; add no policy here."""
        return AdapterRequest(
            operation=local_event["operation"],
            payload=dict(local_event["payload"]),
            session_id=local_event["session_id"],
            session_state=local_event["session_state"],
        )

    def translate_response(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Map the shared result while preserving accounting and attribution."""
        return {
            "payload": dict(envelope["payload"]),
            "provider_accounting": dict(envelope["provider_accounting"]),
            "context_attribution": envelope.get("context_attribution"),
            "error": envelope.get("error"),
        }

    def readiness_signal(self) -> dict[str, Any]:
        return {"status": "ready"}

    def rollback_signal(self) -> dict[str, Any]:
        return {"status": "inactive", "rollback_reference": None}
