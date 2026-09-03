"""Map canonical operations into normalized Cursor hook events."""


def _hook(operation: str) -> str:
    prefix = operation.split(".", 1)[0]
    if operation == "lifecycle.shutdown":
        return "sessionEnd"
    return {
        "lifecycle": "sessionStart",
        "message": "beforeSubmitPrompt",
        "tool": "preToolUse",
        "mcp": "beforeMCPExecution",
    }.get(prefix, "preCompact")


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "hook_event_name": _hook(operation), "operation": operation,
        "input": payload, "conversation_id": session_id, "session_state": state,
    }
