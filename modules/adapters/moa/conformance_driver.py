"""Map canonical operations into MOA phase events."""


def _phase(operation: str) -> str:
    prefix = operation.split(".", 1)[0]
    return {"lifecycle": "lifecycle", "message": "propose", "authority": "critique", "telemetry": "aggregate"}.get(prefix, "event")


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "phase": _phase(operation), "operation": operation, "content": payload,
        "session": {"id": session_id, "state": state},
    }
