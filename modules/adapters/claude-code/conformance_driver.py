"""Map canonical operations into Claude Code hook events."""


def _hook(operation: str) -> str:
    prefix = operation.split(".", 1)[0]
    if operation == "lifecycle.shutdown":
        return "Stop"
    return {"lifecycle": "SessionStart", "message": "UserPromptSubmit", "tool": "PreToolUse"}.get(prefix, "Notification")


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "hook_event_name": _hook(operation), "operation": operation,
        "tool_input": payload, "session_id": session_id, "session_state": state,
    }
