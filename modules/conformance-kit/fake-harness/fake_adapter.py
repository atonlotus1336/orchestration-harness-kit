"""Translation-only adapter for the provider-free conformance harness."""

from __future__ import annotations

from typing import Any

from lapart_adapter import AdapterRequest, HarnessAdapter


class FakeHarnessAdapter(HarnessAdapter):
    def initialize(self, config_reference: str, data_reference: str) -> dict[str, Any]:
        return {"ready": True, "config_reference": config_reference, "data_reference": data_reference}

    def shutdown(self, reason: str) -> dict[str, Any]:
        return {"closed": True, "reason": reason}

    def translate_request(self, local_event: Any) -> AdapterRequest:
        if not isinstance(local_event, dict) or set(local_event) != {"op", "body", "conversation", "state"}:
            raise ValueError("fake harness event keys are not closed")
        return AdapterRequest(
            operation=local_event["op"],
            payload=dict(local_event["body"]),
            session_id=local_event["conversation"],
            session_state=local_event["state"],
        )

    def translate_response(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": envelope["kind"],
            "op": envelope["operation"],
            "conversation": dict(envelope["session"]),
            "body": dict(envelope["payload"]),
            "provider_accounting": dict(envelope["provider_accounting"]),
            "context_attribution": envelope.get("context_attribution"),
            "error": envelope.get("error"),
        }

    def readiness_signal(self) -> dict[str, Any]:
        return {"ready": True, "provider_calls": 0}

    def rollback_signal(self) -> dict[str, Any]:
        return {"state": "inactive", "rollback_reference": None}
