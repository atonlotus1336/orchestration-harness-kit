"""Map canonical operations into Antigravity CLI events."""

from __future__ import annotations


def _command(operation: str) -> str:
    prefix = operation.split(".", 1)[0]
    return {
        "lifecycle": "lifecycle",
        "message": "print",
        "tool": "tool",
    }.get(prefix, "session")


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "command": _command(operation),
        "operation": operation,
        "arguments": payload,
        "session": {"id": session_id, "state": state},
    }
