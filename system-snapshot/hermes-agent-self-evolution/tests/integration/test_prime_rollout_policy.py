import json
from pathlib import Path

import pytest

from prime_bridge.rollout_policy import REQUIRED_CHECKS, RolloutPolicy, RolloutPolicyError


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/prime-rollout-policy.json"


def test_closed_broad_policy_preserves_native_safeguards():
    policy = RolloutPolicy.load(POLICY)
    assert policy.stage == "broad" and policy.enabled is True
    assert policy.profile_eligible("workspace") and policy.profile_eligible("home")
    assert policy.profile_eligible("telegram")
    assert policy.required_update_checks == REQUIRED_CHECKS
    assert policy.admission.unknown_resource_action == "reject-prime"
    assert policy.native_fallback and policy.explicit_handoff_required


@pytest.mark.parametrize("mutation", ["key", "coverage", "check", "resource", "reserve", "cohort"])
def test_policy_weakening_fails_closed(tmp_path, mutation):
    value = json.loads(POLICY.read_text(encoding="utf-8"))
    if mutation == "key":
        value["silent_enable"] = True
    elif mutation == "coverage":
        value["minimum_eligible_coverage_percent"] = 0
    elif mutation == "check":
        value["required_update_checks"].remove("native-hermes")
    elif mutation == "resource":
        value["admission"]["unknown_resource_action"] = "allow"
    elif mutation == "reserve":
        value["admission"]["native_reserve_percent"] = 0
    else:
        value["excluded_profiles"].append("workspace")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RolloutPolicyError):
        RolloutPolicy.load(path)
