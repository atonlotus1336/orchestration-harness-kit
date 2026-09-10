#!/usr/bin/env python3
"""Provider-free validation for the optional additive-parallel feature."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


MODULE = Path(__file__).resolve().parent
PROFILE_PATH = MODULE / "additive-parallel.profile.json"
OBSERVE_OPERATIONS = ("bind-orca", "observe-orca", "observe-orca-worker-show")
ELIGIBLE_OPERATIONS = ("inspect", "search", "analyze", "review", "compare")
PROHIBITED = (
    "file_writes", "database_mutation", "service_changes", "external_side_effects",
    "parallel_coding", "recursive_delegation", "provider_or_model_changes",
    "new_provider_consumption",
)


class ProfileError(ValueError):
    """Raised when a feature profile or local receipt is unsafe."""


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProfileError("profile must be an object")
    return value


def profile_hash(profile: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_profile(profile: dict[str, Any], *, shipped: bool = False) -> None:
    required = {"schema", "feature_state", "core_activation_state", "limits", "admission", "prohibited_capabilities", "fallback", "orca"}
    if set(profile) != required or profile.get("schema") != "lapart.additive-parallel-profile.v1":
        raise ProfileError("profile schema is invalid")
    feature, core = profile["feature_state"], profile["core_activation_state"]
    if feature not in {"inactive", "shadow"} or core not in {"inactive", "shadow"}:
        raise ProfileError("feature and core activation states are invalid")
    if (feature == "inactive") != (core == "inactive") or (shipped and feature != "inactive"):
        raise ProfileError("shipped profile must remain inactive and map state exactly")
    limits = profile["limits"]
    if limits != {"max_concurrent_children": 2, "minimum_independent_tasks": 2}:
        raise ProfileError("parallel limits must be exactly two independent children")
    admission = profile["admission"]
    required_admission = {
        "eligible_operations", "require_independent_tasks", "require_no_shared_mutable_state",
        "require_frozen_context_references", "require_bounded_assignments",
        "parent_only_synthesis", "parent_verification_required",
    }
    if set(admission) != required_admission or tuple(admission["eligible_operations"]) != ELIGIBLE_OPERATIONS:
        raise ProfileError("admission contract is invalid")
    if any(admission[key] is not True for key in required_admission - {"eligible_operations"}):
        raise ProfileError("parent and admission safeguards must be required")
    prohibited = profile["prohibited_capabilities"]
    if set(prohibited) != set(PROHIBITED) or any(prohibited.get(key) is not False for key in PROHIBITED):
        raise ProfileError("mutating, recursive, and provider capabilities must remain disabled")
    fallback = profile["fallback"]
    if fallback != {"mode": "serial", "on_uncertain_independence": True, "on_unsupported_control": True, "on_failed_admission": True, "preserve_evidence": True}:
        raise ProfileError("serial fallback contract is invalid")
    orca = profile["orca"]
    expected_orca = {
        "mode": "observation_only", "allowed_operations": list(OBSERVE_OPERATIONS),
        "may_launch_or_resume": False, "may_issue_commands": False, "may_approve": False,
        "may_select_review": False, "may_complete_canonical_job": False,
        "polling_loop_installed": False,
    }
    if orca != expected_orca:
        raise ProfileError("Orca must remain observation-only")


def admit_batch(profile: dict[str, Any], children: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a deterministic serial fallback or a bounded read-only admission."""
    validate_profile(profile)
    if profile["feature_state"] != "shadow":
        return {"decision": "serial", "reason": "FEATURE_INACTIVE", "provider_calls": 0}
    if len(children) != profile["limits"]["minimum_independent_tasks"]:
        return {"decision": "serial", "reason": "CHILD_COUNT_INVALID", "provider_calls": 0}
    for child in children:
        if not isinstance(child, dict) or set(child) != {"id", "operation", "independent", "shared_mutable_state", "frozen_context", "bounded", "read_only"}:
            return {"decision": "serial", "reason": "CHILD_PACKET_INVALID", "provider_calls": 0}
        if child["operation"] not in ELIGIBLE_OPERATIONS or child["independent"] is not True or child["shared_mutable_state"] is not False or child["frozen_context"] is not True or child["bounded"] is not True or child["read_only"] is not True:
            return {"decision": "serial", "reason": "ADMISSION_DENIED", "provider_calls": 0}
    if len({child["id"] for child in children}) != len(children):
        return {"decision": "serial", "reason": "CHILD_IDENTITY_CONFLICT", "provider_calls": 0}
    return {"decision": "parallel_read_only", "children": [child["id"] for child in children], "provider_calls": 0}


def observe_events(job_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Accept only unique, current observations; never turn them into completion."""
    seen: set[str] = set()
    accepted: list[str] = []
    rejected: list[str] = []
    for event in events:
        event_id = event.get("event_id") if isinstance(event, dict) else None
        if not isinstance(event_id, str) or event.get("job_id") != job_id or event.get("status") not in {"running", "done", "cancelled"}:
            rejected.append(str(event_id))
        elif event_id in seen:
            rejected.append(event_id)
        else:
            seen.add(event_id)
            accepted.append(event_id)
    return {"accepted": accepted, "rejected": rejected, "canonical_completion": False, "provider_calls": 0}


def build_receipt(profile: dict[str, Any], approval_reference: str, rollback_reference: str) -> dict[str, Any]:
    validate_profile(profile)
    if profile["feature_state"] != "shadow" or not approval_reference or not rollback_reference:
        raise ProfileError("only a local shadow activation may issue a receipt")
    return {
        "schema": "lapart.additive-parallel-receipt.v1",
        "profile_sha256": profile_hash(profile),
        "prior_feature_state": "inactive",
        "feature_state": "shadow",
        "core_activation_state": "shadow",
        "approval_reference_sha256": hashlib.sha256(approval_reference.encode()).hexdigest(),
        "rollback_reference": rollback_reference,
        "provider_calls": 0,
        "pay_per_token": False,
    }


def _must_fail(profile: dict[str, Any], mutate) -> bool:
    candidate = copy.deepcopy(profile)
    mutate(candidate)
    try:
        validate_profile(candidate, shipped=True)
    except ProfileError:
        return True
    return False


def verify() -> list[str]:
    failures: list[str] = []
    profile = _load(PROFILE_PATH)
    try:
        validate_profile(profile, shipped=True)
    except ProfileError as exc:
        return [str(exc)]
    if not _must_fail(profile, lambda value: value["limits"].update(max_concurrent_children=3)):
        failures.append("max-three profile did not fail closed")
    if not _must_fail(profile, lambda value: value["prohibited_capabilities"].update(file_writes=True)):
        failures.append("write-capable profile did not fail closed")
    if not _must_fail(profile, lambda value: value["orca"].update(may_issue_commands=True)):
        failures.append("Orca command authority did not fail closed")
    active = copy.deepcopy(profile)
    active["feature_state"] = active["core_activation_state"] = "shadow"
    valid = [{"id": "a", "operation": "inspect", "independent": True, "shared_mutable_state": False, "frozen_context": True, "bounded": True, "read_only": True}, {"id": "b", "operation": "review", "independent": True, "shared_mutable_state": False, "frozen_context": True, "bounded": True, "read_only": True}]
    if admit_batch(active, valid).get("decision") != "parallel_read_only":
        failures.append("valid bounded read-only batch was not admitted")
    invalid = copy.deepcopy(valid)
    invalid[1]["shared_mutable_state"] = True
    if admit_batch(active, invalid).get("decision") != "serial":
        failures.append("ambiguous batch did not fall back to serial")
    observation = observe_events("job-1", [{"event_id": "one", "job_id": "job-1", "status": "done"}, {"event_id": "one", "job_id": "job-1", "status": "done"}, {"event_id": "bad", "job_id": "other", "status": "done"}])
    if observation != {"accepted": ["one"], "rejected": ["one", "bad"], "canonical_completion": False, "provider_calls": 0}:
        failures.append("Orca observation integrity is invalid")
    receipt = build_receipt(active, "local-approval", "local-rollback")
    if receipt["provider_calls"] != 0 or receipt["pay_per_token"] is not False or receipt["core_activation_state"] != "shadow":
        failures.append("local receipt is not provider-free shadow evidence")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    failures = verify()
    result = {"status": "PASS" if not failures else "HOLD", "provider_calls": 0, "pay_per_token": False, "failures": failures}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif failures:
        print("HOLD: additive parallel observation")
        for failure in failures:
            print(f"- {failure}")
    else:
        print("PASS: inactive portable profile and provider-free feature conformance")
        print("PASS: max-two read-only admission, serial fallback, parent-only completion, Orca observation-only")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
