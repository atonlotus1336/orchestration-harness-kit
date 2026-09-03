"""Map canonical operations into OpenCode plugin-hook events."""

from __future__ import annotations


def _hook(operation: str) -> str:
    prefix = operation.split(".", 1)[0]
    return {
        "lifecycle": "lifecycle",
        "message": "chat.message",
        "tool": "tool.execute",
    }.get(prefix, "event")


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "hook": _hook(operation),
        "operation": operation,
        "payload": payload,
        "session_id": session_id,
        "session_state": state,
    }
