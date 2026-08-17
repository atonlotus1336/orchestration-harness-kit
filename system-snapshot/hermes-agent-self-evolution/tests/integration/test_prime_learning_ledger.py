import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prime_bridge.learning_ledger import LearningLedger, LearningLedgerError
from prime_bridge.promotion_policy import PromotionPolicy, PromotionPolicyError

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/prime-learning-policy.json"


def evidence():
    return [{"kind": "test", "reference": "pytest:debug-contract", "outcome": "passed"}]


def add(ledger, **changes):
    values = {
        "profile": "home", "project": "__PRIVATE_TOKEN_02__", "session_id": "session-1",
        "scope": "project", "subject": "debug.gateway-binding",
        "content": "Gateway 8642 must bind to 0.0.0.0 for the thin client.",
        "privacy": "internal", "provenance": {"job_id": "a" * 32, "source": "prime"},
        "evidence": evidence(),
    }
    values.update(changes)
    return ledger.record(**values)


def test_learning_is_namespaced_by_session_project_and_profile(tmp_path):
    ledger = LearningLedger(tmp_path / "learning.sqlite3")
    first = add(ledger)
    add(ledger, session_id="session-2", content="Second observation")
    add(ledger, profile="gpt-5.4", content="Other profile observation")
    assert ledger.get_record(first["record_id"])["scope"] == "project"
    assert len(ledger.list_records(profile="home", session_id="session-1")) == 1
    assert len(ledger.list_records(profile="home")) == 2
    assert len(ledger.list_records(profile="gpt-5.4")) == 1


def test_proposal_is_complete_and_validation_is_separate(tmp_path):
    ledger = LearningLedger(tmp_path / "learning.sqlite3")
    record = add(ledger)
    proposal = ledger.propose(record["record_id"], profile="home", lane="fact", target_scope="global")
    for key in (
        "provenance", "evidence", "target_scope", "privacy", "validation",
        "expires_at", "rollback_id", "dedupe_key", "authority_rank",
    ):
        assert key in proposal
    assert proposal["validation"] is None and proposal["scope_widening"] is True
    digest = hashlib.sha256(b"independent probe").hexdigest()
    validated = ledger.validate(
        proposal["proposal_id"], validator="hermes-contract-test",
        mode="independent", outcome="passed", evidence_hash=digest,
    )
    assert validated["status"] == "validated"
    assert validated["validation"]["validator"] == "hermes-contract-test"


def test_secret_shapes_and_cross_profile_proposals_fail_closed(tmp_path):
    ledger = LearningLedger(tmp_path / "learning.sqlite3")
    with pytest.raises(RuntimeError, match="secret"):
        add(ledger, content="Use " + "sk-" + "a" * 30 + " for requests")
    record = add(ledger)
    with pytest.raises(LearningLedgerError, match="another profile"):
        ledger.propose(record["record_id"], profile="gpt-5.4", lane="fact", target_scope="project")


def test_expired_or_invalid_validation_is_rejected(tmp_path):
    ledger = LearningLedger(tmp_path / "learning.sqlite3")
    record = add(ledger)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(LearningLedgerError, match="future"):
        ledger.propose(record["record_id"], profile="home", lane="fact", target_scope="project", expires_at=past)
    proposal = ledger.propose(record["record_id"], profile="home", lane="fact", target_scope="project")
    with pytest.raises(LearningLedgerError, match="evidence hash"):
        ledger.validate(proposal["proposal_id"], validator="host", mode="deterministic", outcome="passed", evidence_hash="bad")


def test_four_lane_policy_is_closed_and_fail_closed(tmp_path):
    policy = PromotionPolicy.load(POLICY)
    assert policy.shared_root.as_posix().endswith("auto-memory/prime-promotions")
    value = json.loads(POLICY.read_text())
    value["unknown"] = True
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(PromotionPolicyError, match="closed"):
        PromotionPolicy.load(bad)


def test_policy_auto_categories_and_protected_lanes(tmp_path):
    ledger = LearningLedger(tmp_path / "learning.sqlite3")
    policy = PromotionPolicy.load(POLICY)
    record = add(ledger)
    proposal = ledger.propose(record["record_id"], profile="home", lane="fact", target_scope="global")
    proposal = ledger.validate(
        proposal["proposal_id"], validator="independent-check",
        mode="independent", outcome="passed", evidence_hash="1" * 64,
    )
    assert policy.decide(proposal).action == "auto_promote"
    protected = dict(proposal, lane="policy", subject="policy.provider-fallback")
    assert policy.decide(protected).action == "approval_required"
    restricted = dict(proposal, privacy="restricted")
    assert policy.decide(restricted).action == "approval_required"
