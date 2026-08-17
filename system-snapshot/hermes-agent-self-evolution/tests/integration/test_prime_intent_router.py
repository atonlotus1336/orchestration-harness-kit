import hashlib
import json
from pathlib import Path

import pytest

from prime_bridge.intent_router import IntentPolicy, IntentPolicyError, classify


ROOT = Path(__file__).resolve().parents[2]
POLICY = IntentPolicy.load(ROOT / "config/prime-auto-route-policy.json")
SKILL = ROOT.parent / "skills-shared/ponytail/skills/ponytail/SKILL.md"


def ponytail():
    return {"available": True, "path": POLICY.ponytail_path, "intensity": "full", "fingerprint": hashlib.sha256(SKILL.read_bytes()).hexdigest()}


@pytest.mark.parametrize("message,family", [
    ("make a gsd plan for phase 9", "gsd_plan"), ("execute phase 9", "gsd_execute"),
    ("phase check 9", "phase_check"), ("doublecheck plan 9", "doublecheck"),
    ("audit phase 9", "audit"), ("debug this failure", "debug"),
    ("diagnose this error", "diagnose"), ("bug fix the routing", "bug_fix"),
])
def test_explicit_operational_requests_are_prime_candidates(message, family):
    result = classify(message, profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] == "PRIME_AUTO" and result["family"] == family
    assert result["ponytail"]["fingerprint"] == ponytail()["fingerprint"]


@pytest.mark.parametrize("message,family", [
    ("Could you make a GSD plan for Phase 10?", "gsd_plan"),
    ("Please execute Phase 10", "gsd_execute"),
    ("I want you to verify Phase 10", "phase_check"),
    ("Can you doublecheck and debug Phase 10?", "doublecheck"),
    ("Could you troubleshoot this repository failure?", "debug"),
    ("Help me fix this bug in the gateway", "bug_fix"),
])
def test_natural_request_wrappers_auto_route_to_prime(message, family):
    result = classify(
        message, profile="workspace", mode="prime-default", readiness=True,
        compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY,
        project_scope=True,
    )
    assert result["outcome"] in {"PRIME_AUTO", "PRIME_CODE"}
    assert result["family"] == family


@pytest.mark.parametrize("message", ["What is Prime Agent?", "please explain how to debug", 'Quote: "audit phase 9"', "hi there"])
def test_conversation_and_examples_stay_native(message):
    result = classify(message, profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] == "NATIVE"


@pytest.mark.parametrize("kwargs,reason", [
    ({"profile": "native-only-test", "mode": "native-only"}, "PROFILE_NOT_ELIGIBLE"),
    ({"readiness": False}, "READINESS_UNVERIFIED"), ({"compatibility": False}, "COMPATIBILITY_UNVERIFIED"),
    ({"admitted": False}, "ADMISSION_REJECTED"), ({"ponytail": {"available": False}}, "PONYTAIL_SKILL_UNAVAILABLE"),
    ({"ponytail": {**ponytail(), "intensity": "ultra"}}, "PONYTAIL_SKILL_MISMATCH"),
])
def test_failed_gates_fall_back_to_native(kwargs, reason):
    values = {"profile": "workspace", "mode": "prime-default", "readiness": True, "compatibility": True, "admitted": True, "ponytail": ponytail()}
    values.update(kwargs)
    result = classify("audit phase 9", policy=POLICY, **values)
    assert result["outcome"] == "NATIVE" and result["reason"] == reason


def test_policy_rejects_noncanonical_ponytail(tmp_path):
    value = json.loads((ROOT / "config/prime-auto-route-policy.json").read_text())
    value["ponytail"]["intensity"] = "ultra"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    with pytest.raises(IntentPolicyError):
        IntentPolicy.load(path)


# --- Phase 11: explicit Ponytail invocation is Prime-required ---

# Accepted explicit invocation forms (case-insensitive).
PONYTAIL_INVOCATIONS = [
    "run ponytail",
    "use ponytail",
    "$ponytail",
    "[$ponytail:ponytail]",
    "Run Ponytail",
    "USE PONYTAIL",
    "please run ponytail on this repo",
    "use ponytail to refactor the module",
    "run ponytail and implement the feature",
]


@pytest.mark.parametrize("message", PONYTAIL_INVOCATIONS)
def test_ponytail_invocation_routes_to_prime(message):
    result = classify(message, profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] in {"PRIME_AUTO", "PRIME_CODE"}
    assert result["family"] == "ponytail_invocation"


@pytest.mark.parametrize("message", [
    "run ponytail and implement the feature",
    "use ponytail to refactor the module",
    "run ponytail to create tests",
])
def test_ponytail_invocation_with_code_action_routes_to_prime_code(message):
    result = classify(message, profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY, project_scope=True)
    assert result["outcome"] == "PRIME_CODE" and result["family"] == "ponytail_invocation"


@pytest.mark.parametrize("message", [
    "run ponytail",
    "use ponytail",
    "$ponytail",
    "[$ponytail:ponytail]",
])
def test_ponytail_invocation_without_code_action_routes_to_prime_auto(message):
    result = classify(message, profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] == "PRIME_AUTO" and result["family"] == "ponytail_invocation"


# False positives: "ponytail" in quoted text or discussion must NOT trigger.
@pytest.mark.parametrize("message", [
    "what is ponytail?",
    "discuss ponytail",
    "the ponytail skill",
    "can you explain ponytail",
    "I read about ponytail in the docs",
])
def test_ponytail_mention_in_discussion_stays_native(message):
    result = classify(message, profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] == "NATIVE"


# Gate failure on an explicit Ponytail invocation hard-blocks (no native fallback).
@pytest.mark.parametrize("kwargs,reason", [
    ({"readiness": False}, "READINESS_UNVERIFIED"),
    ({"compatibility": False}, "COMPATIBILITY_UNVERIFIED"),
    ({"admitted": False}, "ADMISSION_REJECTED"),
])
def test_ponytail_invocation_gate_failure_blocks_with_prime_required_unavailable(kwargs, reason):
    values = {"profile": "workspace", "mode": "prime-default", "readiness": True, "compatibility": True, "admitted": True, "ponytail": ponytail()}
    values.update(kwargs)
    result = classify("run ponytail", policy=POLICY, **values)
    assert result["outcome"] == "PRIME_REQUIRED_UNAVAILABLE"
    assert result["reason"] == reason
    assert result["family"] == "ponytail_invocation"


# Regression: existing families are unchanged.
@pytest.mark.parametrize("message,family", [
    ("make a gsd plan for phase 9", "gsd_plan"), ("execute phase 9", "gsd_execute"),
    ("phase check 9", "phase_check"), ("doublecheck plan 9", "doublecheck"),
    ("audit phase 9", "audit"), ("debug this failure", "debug"),
    ("diagnose this error", "diagnose"), ("bug fix the routing", "bug_fix"),
])
def test_existing_families_unchanged_by_ponytail_detector(message, family):
    result = classify(message, profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] == "PRIME_AUTO" and result["family"] == family


def test_non_ponytail_gate_failure_still_native():
    result = classify("audit phase 9", profile="workspace", mode="prime-default", readiness=False, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] == "NATIVE" and result["reason"] == "READINESS_UNVERIFIED"
