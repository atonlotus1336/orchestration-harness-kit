"""Map canonical operations into Pi extension events."""

from __future__ import annotations


def _event(operation: str) -> str:
    prefix = operation.split(".", 1)[0]
    return {
        "lifecycle": "lifecycle",
        "message": "message",
        "tool": "tool_call",
    }.get(prefix, "before_agent_start")


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "event": _event(operation),
        "operation": operation,
        "data": payload,
        "session": {"id": session_id, "state": state},
    }
