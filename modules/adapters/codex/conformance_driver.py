"""Map canonical operations into Codex notification events."""


def _notification(operation: str) -> str:
    return {"lifecycle": "lifecycle", "message": "turn", "tool": "item"}.get(operation.split(".", 1)[0], "event")


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "notification": _notification(operation), "operation": operation,
        "input": payload, "thread": {"id": session_id, "state": state},
    }
