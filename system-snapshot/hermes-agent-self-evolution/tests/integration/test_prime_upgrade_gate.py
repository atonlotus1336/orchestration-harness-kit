from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prime_bridge.rollout_policy import RolloutPolicy
from prime_bridge.upgrade_gate import Candidate, UpdateGate, UpgradeGateError


ROOT = Path(__file__).resolve().parents[2]
POLICY = RolloutPolicy.load(ROOT / "config/prime-rollout-policy.json")
CANDIDATE = {
    "source_commit": "a" * 40,
    "image_digest": "sha256:" + "b" * 64,
    "command_fingerprint": "c" * 64,
    "prime_protocol": "v0.7.1-rpc",
    "bridge_version": "1.0",
}


def evidence(*, now=None, status="PASS", fingerprint=None):
    instant = now or datetime.now(timezone.utc)
    digest = fingerprint or Candidate.parse(CANDIDATE).fingerprint
    return {
        name: {"status": status, "candidate_fingerprint": digest, "checked_at": instant.isoformat()}
        for name in POLICY.required_update_checks
    }


def test_exact_candidate_and_all_independent_checks_are_ready():
    first = UpdateGate(POLICY).evaluate(CANDIDATE, evidence(), native_status="PASS", profile_coverage_percent=96)
    second = UpdateGate(POLICY).evaluate(CANDIDATE, evidence(), native_status="PASS", profile_coverage_percent=96)
    assert first["status"] == "READY" and first["reasons"] == []
    assert first["decision_fingerprint"] == second["decision_fingerprint"]
    assert "command_fingerprint" not in first


@pytest.mark.parametrize("mutation", ["missing", "failed", "mismatch", "stale", "native", "coverage"])
def test_every_load_bearing_input_can_block(mutation):
    checks = evidence()
    native, coverage = "PASS", 96
    if mutation == "missing":
        checks.pop("rpc-contract")
    elif mutation == "failed":
        checks["containment"]["status"] = "FAIL"
    elif mutation == "mismatch":
        checks["model-route"]["candidate_fingerprint"] = "0" * 64
    elif mutation == "stale":
        checks = evidence(now=datetime.now(timezone.utc) - timedelta(hours=1))
    elif mutation == "native":
        native = "FAIL"
    else:
        coverage = 10
    result = UpdateGate(POLICY).evaluate(CANDIDATE, checks, native_status=native, profile_coverage_percent=coverage)
    assert result["status"] == "BLOCKED" and result["reasons"]
    assert result["native_fallback"] is True


def test_candidate_schema_and_identity_are_closed():
    with pytest.raises(UpgradeGateError):
        Candidate.parse({**CANDIDATE, "mutable_tag": "latest"})
    with pytest.raises(UpgradeGateError):
        Candidate.parse({**CANDIDATE, "image_digest": "latest"})
