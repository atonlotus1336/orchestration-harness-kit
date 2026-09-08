"""Translate documented native completion events into the shared evidence gate."""
from collections.abc import Mapping


def normalize(payload, harness):
    if not isinstance(payload, Mapping):
        raise ValueError("native payload must be an object")
    result = dict(payload)
    if harness == "cursor":
        candidates = [payload[k] for k in ("session_id", "conversation_id") if payload.get(k)]
        if any(not isinstance(v, str) for v in candidates) or len(set(candidates)) > 1:
            raise ValueError("conflicting native conversation identities")
        if candidates:
            result["session_id"] = candidates[0]
        event = payload.get("hook_event_name")
        result["hook_event_name"] = {"stop": "Stop", "preToolUse": "PreToolUse"}.get(event, event)
        if event == "stop" and payload.get("status") != "completed":
            result["hook_event_name"] = "Interrupted"
        if not result.get("cwd") and len(payload.get("workspace_roots", [])) == 1:
            result["cwd"] = payload["workspace_roots"][0]
    elif harness == "antigravity":
        candidates = [payload[k] for k in ("session_id", "conversationId") if payload.get(k)]
        if any(not isinstance(v, str) for v in candidates) or len(set(candidates)) > 1:
            raise ValueError("conflicting native conversation identities")
        if candidates:
            result["session_id"] = candidates[0]
        roots = payload.get("workspacePaths", [])
        if not result.get("cwd") and isinstance(roots, list) and len(roots) == 1:
            result["cwd"] = roots[0]
        tool = payload.get("toolCall")
        if isinstance(tool, Mapping):
            names = {"write_to_file": "Write", "replace_file_content": "Edit",
                     "multi_replace_file_content": "MultiEdit"}
            result["tool_name"] = names.get(tool.get("name"), tool.get("name"))
            args = tool.get("args") or {}
            if not isinstance(args, Mapping):
                raise ValueError("native tool arguments must be an object")
            result["tool_input"] = dict(args)
            if args.get("TargetFile"):
                result["tool_input"]["file_path"] = args["TargetFile"]
        if payload.get("hook_event_name") == "Stop" and (
            payload.get("fullyIdle") is not True or payload.get("terminationReason") != "model_stop"
            or payload.get("error")
        ):
            result["hook_event_name"] = "Interrupted"
    elif harness in {"pi", "opencode", "dsh"}:
        event = payload.get("hook_event_name")
        result["hook_event_name"] = {
            "agent_end": "Stop", "agent_settled": "Stop", "session.idle": "Stop", "session/prompt:complete": "Stop",
            "tool_call": "PreToolUse", "tool.execute.before": "PreToolUse",
        }.get(event, event)
        if payload.get("aborted") is True:
            result["hook_event_name"] = "Interrupted"
    return result


def response(result, payload, harness):
    if result["action"] != "block":
        return {}
    reason = result["reason"]
    if harness == "codex" and payload.get("hook_event_name") == "Stop":
        if result.get("workflow_action") == "remediate" and payload.get("stop_hook_active") is not True:
            return {"decision": "block", "reason": reason}
        message = "Review incomplete. " + reason
        if payload.get("stop_hook_active") is True:
            message = "Loop Limit Reached - Escape Hatch. " + message
        return {"continue": False, "stopReason": message, "systemMessage": message}
    if harness == "cursor":
        if payload.get("hook_event_name") == "stop":
            if payload.get("status") != "completed":
                return {}
            count = payload.get("loop_count", 0)
            if type(count) is not int or count >= 2:
                return {"followup_message": ""}
            return {"followup_message": reason}
        return {"permission": "deny", "user_message": reason, "agent_message": reason}
    if harness in {"codex", "claude-code", "dsh"} and payload.get("hook_event_name") == "PreToolUse":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                "permissionDecision": "deny", "permissionDecisionReason": reason}}
    return {"decision": "block", "reason": reason}


def inspect_opencode_completion(events):
    """Require one completed native session and accounted model steps."""
    if not events or any(not isinstance(e, dict) for e in events):
        raise ValueError("Native events are missing or malformed.")
    sessions = {e["sessionID"] for e in events if e.get("sessionID")}
    if len(sessions) != 1 or any(e.get("type") == "error" for e in events):
        raise ValueError("Native session identity or outcome is missing.")
    session = sessions.pop()
    if not isinstance(session, str):
        raise ValueError("Native session identity is invalid.")
    steps = [e for e in events if e.get("type") == "step_finish"]
    if not steps or any(e.get("sessionID") != session for e in steps):
        raise ValueError("Native completion accounting is missing.")
    usage = []
    for event in steps:
        part = event.get("part")
        tokens = part.get("tokens") if isinstance(part, dict) else None
        if not isinstance(tokens, dict) or any(
            type(tokens.get(key)) is not int or tokens[key] < 0
            for key in ("input", "output", "total")
        ):
            raise ValueError("Native usage is incomplete.")
        usage.append(tokens)
    if steps[-1]["part"].get("reason") != "stop":
        raise ValueError("Native run did not reach normal completion.")
    return session, usage

def inspect_cursor_completion(value,session):
 if not isinstance(value,dict) or value.get('is_error') is not False or value.get('subtype')!='success':raise ValueError('native run did not succeed')
 if value.get('session_id')!=session:raise ValueError('native session identity mismatch')
 usage=value.get('usage')
 if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in ['inputTokens','outputTokens']):raise ValueError('native usage incomplete')
 return usage

def review_allows_release(result):
    review = result.get("review_completion", {})
    return (result.get("action") == "allow"
            and review.get("mode") == "enforced"
            and review.get("review_completed") is True
            and review.get("consumption", {}).get("review_passed") is True)
