import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"modules/orchestration-core"))
from lapart_core.native_review import normalize,response
@pytest.mark.parametrize("harness",["codex","claude-code","dsh"])
def test_pretool_denial_uses_native_permission_contract(harness):
    result=response({"action":"block","reason":"missing baseline"},{"hook_event_name":"PreToolUse"},harness)
    assert result["hookSpecificOutput"]["permissionDecision"]=="deny"
def test_cursor_abort_and_loop_limit_do_not_restart_work():
    result={"action":"block","reason":"incomplete"}
    assert response(result,{"hook_event_name":"stop","status":"aborted"},"cursor")=={}
    assert response(result,{"hook_event_name":"stop","status":"completed","loop_count":2},"cursor")=={"followup_message":""}
def test_cursor_rejects_conflicting_session_identity():
    with pytest.raises(ValueError):normalize({"session_id":"one","conversation_id":"two"},"cursor")


def test_codex_failed_evidence_stops_instead_of_restarting():
    value=response({"action":"block","reason":"EVIDENCE_INTEGRITY_FAILED"},{"hook_event_name":"Stop"},"codex")
    assert value["continue"] is False and "decision" not in value
    assert "Review incomplete" in value["systemMessage"]

def test_codex_proven_remediation_stops_after_one_continuation():
    result={"action":"block","reason":"proven finding","workflow_action":"remediate"}
    assert response(result,{"hook_event_name":"Stop"},"codex")["decision"]=="block"
    assert response(result,{"hook_event_name":"Stop","stop_hook_active":True},"codex")["continue"] is False

@pytest.mark.parametrize("patch",[{"fullyIdle":False},{"terminationReason":"error"},{"error":"failed"}])
def test_antigravity_requires_clean_idle_stop(patch):
    value={"hook_event_name":"Stop","conversationId":"one","fullyIdle":True,"terminationReason":"model_stop"}
    value.update(patch)
    assert normalize(value,"antigravity")["hook_event_name"]=="Interrupted"

def test_antigravity_conflicting_identity_is_not_accepted():
    with pytest.raises(ValueError):normalize({"session_id":"one","conversationId":"two"},"antigravity")

from lapart_core.native_review import inspect_opencode_completion, inspect_cursor_completion, review_allows_release

def completion():
    return [{"type": "step_finish", "sessionID": "session-1", "part": {
        "reason": "stop", "tokens": {"input": 20, "output": 5, "total": 25}}}]

def allowed():
    return {"action": "allow", "review_completion": {
        "mode": "enforced", "review_completed": True,
        "consumption": {"review_passed": True}}}

def test_accounted_native_completion_is_accepted():
    session, usage = inspect_opencode_completion(completion())
    assert session == "session-1" and usage[0]["total"] == 25

@pytest.mark.parametrize("events", [[], [None], [{"type": "text", "sessionID": "s"}],
    completion() + [{"type": "error", "sessionID": "session-1"}],
    completion() + [{"type": "text", "sessionID": "other"}]])
def test_missing_failed_or_mixed_native_session_rejected(events):
    with pytest.raises(ValueError): inspect_opencode_completion(events)

@pytest.mark.parametrize("value", [None, -1, True, "5"])
@pytest.mark.parametrize("key", ["input", "output", "total"])
def test_incomplete_usage_rejected(key, value):
    events = completion(); events[0]["part"]["tokens"][key] = value
    with pytest.raises(ValueError): inspect_opencode_completion(events)

@pytest.mark.parametrize("reason", [None, "length", "tool-calls", "error"])
def test_nonterminal_or_truncated_native_output_rejected(reason):
    events = completion(); events[0]["part"]["reason"] = reason
    with pytest.raises(ValueError): inspect_opencode_completion(events)

@pytest.mark.parametrize("change", ["bare_allow", "shadow", "unfinished", "failed", "block"])
def test_allow_alone_cannot_release_response(change):
    result = allowed()
    if change == "bare_allow": result.pop("review_completion")
    if change == "shadow": result["review_completion"]["mode"] = "shadow"
    if change == "unfinished": result["review_completion"]["review_completed"] = False
    if change == "failed": result["review_completion"]["consumption"]["review_passed"] = False
    if change == "block": result["action"] = "block"
    assert review_allows_release(result) is False

def test_enforced_completed_review_pass_allows_release():
    assert review_allows_release(allowed()) is True

@pytest.mark.parametrize("patch", [{"session_id":"other"}, {"is_error":True}, {"usage":{}}, {"subtype":"error"}])
def test_portable_cursor_rejects_unverified_native_completion(patch):
    result={"session_id":"expected","is_error":False,"subtype":"success","usage":{"inputTokens":20,"outputTokens":5}}
    result.update(patch)
    with pytest.raises(ValueError): inspect_cursor_completion(result,"expected")

def test_portable_cursor_accepts_exact_accounted_completion():
    result={"session_id":"expected","is_error":False,"subtype":"success","usage":{"inputTokens":20,"outputTokens":5}}
    assert inspect_cursor_completion(result,"expected")==result["usage"]
