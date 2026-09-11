"""Deterministic S4 candidate rules; no dispatch or execution authorization.

The host supplies verified observations. This classifier cannot authenticate
those observations, reserve a budget, or turn a boolean into blocking evidence.
"""
import hashlib
import json
import re
from pathlib import PurePosixPath

SCENARIOS = {
    "milestone_system_gate": "A defined milestone reaches its required system-review gate.",
    "precompletion_system_gate": "The fixed endpoint requires a final system review.",
    "material_architecture_change": "Changed architecture spans at least three subsystems and two structural files.",
    "goalpost_cross_system_weakness": "An active-work goalpost is due and a verified cross-system weakness remains.",
}
BOOL_FIELDS = {
    "endpoint_fixed", "goal_open", "safe_boundary", "source_verified",
    "system_gate_required", "active_work_due", "cross_system_weakness",
    "loop_limit_reached", "same_source_pending", "budget_known",
}
FIELDS = BOOL_FIELDS | {
    "event", "goal_id", "contract_hash", "source_identity", "previous_s4_source",
    "explicit_action", "changed_paths", "structural_paths", "subsystem_count",
    "remaining_budget",
}
EVENTS = {"checkpoint", "milestone", "pre_completion", "active_work_goalpost"}
BUDGET_FIELDS = {
    "max_files", "max_context_bytes", "max_model_calls",
    "max_estimated_input_tokens", "max_tool_calls", "max_elapsed_seconds",
    "max_lens_excerpts", "max_output_tokens", "max_total_tokens",
}


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def recommend_s4(facts, policy):
    """Return a candidate only. A qualified host must independently admit/run it."""
    result = {
        "schema_version": "s4-scenarios.v1", "status": "DEFERRED",
        "scenarios": [], "reasons": [], "automatic_start_authorized": False,
        "model_calls": 0, "required_scope": "system", "deduplication_key": None,
    }

    def answer(status, *reasons):
        result.update(status=status, reasons=list(reasons))
        return result

    if not isinstance(facts, dict) or set(facts) != FIELDS:
        return answer("DEFERRED", "missing-or-unknown-observation-fields")
    if any(type(facts[k]) is not bool for k in BOOL_FIELDS):
        return answer("DEFERRED", "observations-must-be-explicit-booleans")
    if not isinstance(facts["explicit_action"], str) or facts["explicit_action"] not in {"none", "Run now", "Later", "Skip"}:
        return answer("DEFERRED", "unknown-explicit-action")
    if facts["explicit_action"] in {"Later", "Skip"}:
        return answer("DEFERRED", "explicit-user-deferral")
    if not facts["goal_open"]:
        return answer("NOT_NEEDED", "goal-closed-do-not-reopen")
    if facts["loop_limit_reached"]:
        return answer("CHECKPOINT", "Loop Limit Reached - Escape Hatch")
    if not isinstance(facts["event"], str) or facts["event"] not in EVENTS:
        return answer("DEFERRED", "unknown-event")
    if not isinstance(facts["goal_id"], str) or not facts["goal_id"].strip():
        return answer("DEFERRED", "goal-identity-required")
    if not _hash(facts["contract_hash"]) or not _hash(facts["source_identity"]):
        return answer("DEFERRED", "contract-and-source-fingerprints-required")
    previous = facts["previous_s4_source"]
    if previous is not None and not _hash(previous):
        return answer("DEFERRED", "invalid-previous-review-identity")
    if previous == facts["source_identity"] or facts["same_source_pending"]:
        return answer("NOT_NEEDED", "source-already-reviewed-or-pending")
    paths, structural = facts["changed_paths"], facts["structural_paths"]
    if any(not isinstance(v, list) or any(not isinstance(p, str) or not p for p in v)
           for v in (paths, structural)):
        return answer("DEFERRED", "changed-scope-required")
    if (len(set(paths)) != len(paths) or len(set(structural)) != len(structural)
            or not set(structural).issubset(paths)
            or any(PurePosixPath(p).is_absolute() or ".." in PurePosixPath(p).parts
                   or ":" in p or chr(92) in p or PurePosixPath(p).as_posix() != p
                   for p in paths)):
        return answer("DEFERRED", "structural-evidence-outside-changed-scope")
    if type(facts["subsystem_count"]) is not int or facts["subsystem_count"] < 0:
        return answer("DEFERRED", "invalid-subsystem-count")
    if not paths:
        return answer("NOT_NEEDED", "no-source-change")
    if any(not facts[k] for k in ("endpoint_fixed", "safe_boundary", "source_verified")):
        return answer("DEFERRED", "fixed-endpoint-safe-boundary-and-fresh-host-evidence-required")

    scenarios = []
    if facts["event"] == "milestone" and facts["system_gate_required"]:
        scenarios.append("milestone_system_gate")
    if facts["event"] == "pre_completion" and facts["system_gate_required"]:
        scenarios.append("precompletion_system_gate")
    if facts["subsystem_count"] >= 3 and len(set(structural)) >= 2:
        scenarios.append("material_architecture_change")
    if (facts["event"] == "active_work_goalpost" and facts["active_work_due"]
            and facts["cross_system_weakness"] and facts["subsystem_count"] >= 2):
        scenarios.append("goalpost_cross_system_weakness")
    result["scenarios"] = scenarios
    if not scenarios:
        return answer("NOT_NEEDED", "no-system-review-scenario")

    budgets = policy.get("budgets") if isinstance(policy, dict) else None
    required = budgets.get("S4") if isinstance(budgets, dict) else None
    remaining = facts["remaining_budget"]
    if (not facts["budget_known"] or not isinstance(required, dict)
            or set(required) != BUDGET_FIELDS or not isinstance(remaining, dict)
            or set(remaining) != BUDGET_FIELDS
            or any(type(v) is not int or v < 0 for v in remaining.values())
            or any(type(v) is not int or v < 0 for v in required.values())):
        return answer("DEFERRED", "known-complete-budget-envelope-required")
    if any(remaining[k] < required[k] for k in BUDGET_FIELDS):
        return answer("DEFERRED", "full-S4-reservation-does-not-fit-existing-budget")
    if len(paths) > required["max_files"]:
        return answer("DEFERRED", "scope-exceeds-S4-file-budget")
    # Stable across event IDs and harness handoffs. Host must persist and claim
    # this key atomically before spending for the same goal contract and source.
    identity = [facts["goal_id"], facts["contract_hash"], facts["source_identity"]]
    result["deduplication_key"] = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    return answer("CANDIDATE", "requires-qualified-host-admission-and-atomic-budget-reservation")
