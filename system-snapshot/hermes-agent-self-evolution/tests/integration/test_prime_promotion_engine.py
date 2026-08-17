import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prime_bridge.learning_ledger import LearningLedger
from prime_bridge.promotion_engine import PromotionEngine
from prime_bridge.promotion_policy import PromotionPolicy

ROOT = Path(__file__).resolve().parents[2]


def add(ledger, content="Gateway 8642 must bind to 0.0.0.0.", subject="debug.gateway-binding"):
    return ledger.record(
        profile="home", project="__PRIVATE_TOKEN_02__", session_id="session-1", scope="project",
        subject=subject, content=content, privacy="internal",
        provenance={"job_id": "a" * 32, "source": "prime"},
        evidence=[{"kind": "probe", "reference": "gateway-health", "outcome": "passed"}],
    )


def ready(ledger, record, *, lane="fact", scope="global", rank=10, mode="independent"):
    proposal = ledger.propose(
        record["record_id"], profile="home", lane=lane,
        target_scope=scope, authority_rank=rank,
    )
    return ledger.validate(
        proposal["proposal_id"], validator="host-verifier", mode=mode,
        outcome="passed", evidence_hash=hashlib.sha256(record["content"].encode()).hexdigest(),
    )


def components(tmp_path):
    ledger = LearningLedger(tmp_path / "learning.sqlite3")
    policy = PromotionPolicy.load(ROOT / "config/prime-learning-policy.json")
    engine = PromotionEngine(ledger, policy, shared_root=tmp_path / "shared")
    return ledger, engine


def test_verified_fact_auto_promotes_to_scoped_shared_memory(tmp_path):
    ledger, engine = components(tmp_path)
    proposal = ready(ledger, add(ledger))
    promoted = engine.evaluate(proposal["proposal_id"])
    assert promoted["status"] == "promoted"
    artifact = engine.shared_root / promoted["artifact"]["artifact"]
    assert artifact.is_file() and "0.0.0.0" in artifact.read_text()
    assert promoted["rollback_id"] in artifact.read_text()


def test_deterministically_validated_project_procedure_auto_promotes(tmp_path):
    ledger, engine = components(tmp_path)
    record = add(
        ledger, subject="setup.gateway-recovery",
        content="Run the bounded watchdog once, then verify the health endpoint.",
    )
    proposal = ready(ledger, record, lane="procedure", scope="project", mode="deterministic")
    assert engine.evaluate(proposal["proposal_id"])["status"] == "promoted"


def test_duplicate_is_collapsed_and_higher_authority_conflict_blocks(tmp_path):
    ledger, engine = components(tmp_path)
    record = add(ledger)
    first = ready(ledger, record, rank=50)
    engine.evaluate(first["proposal_id"])
    duplicate = ready(ledger, record, rank=50)
    assert engine.evaluate(duplicate["proposal_id"])["status"] == "deduplicated"
    changed = add(ledger, content="Gateway 8642 should bind only to loopback.")
    conflict = ready(ledger, changed, rank=10)
    blocked = engine.evaluate(conflict["proposal_id"])
    assert blocked["status"] == "conflict"
    assert "authority" in blocked["decision"]


def test_policy_lane_remains_approval_gated(tmp_path):
    ledger, engine = components(tmp_path)
    record = add(ledger, subject="policy.provider-fallback", content="Change global provider fallback.")
    proposal = ready(ledger, record, lane="policy", scope="global")
    pending = engine.evaluate(proposal["proposal_id"])
    assert pending["status"] == "approval_required"
    approved = engine.resolve(proposal["proposal_id"], decision="approved", approver="hermes-home")
    assert approved["status"] == "promoted"


def test_expiry_and_revocation_preserve_audit_and_rollback_bytes(tmp_path):
    ledger, engine = components(tmp_path)
    promoted = engine.evaluate(ready(ledger, add(ledger))["proposal_id"])
    active_path = engine.shared_root / promoted["artifact"]["artifact"]
    original = active_path.read_bytes()
    revoked = engine.revoke(promoted["proposal_id"], approver="hermes-home", reason="superseded by verified correction")
    assert revoked["status"] == "revoked" and not active_path.exists()
    rollback = engine.shared_root / revoked["artifact"]["rollback_artifact"]
    assert rollback.read_bytes() == original
    assert ledger.events(promoted["proposal_id"])[-1]["kind"] == "promotion_revoked"

    pending = ledger.propose(add(ledger, subject="debug.expiring")["record_id"], profile="home", lane="fact", target_scope="project")
    ledger.update(
        pending["proposal_id"], expected={"proposed"}, status="proposed",
        changes={"expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
        event="test_clock_advanced",
    )
    assert engine.expire(profile="home") == [pending["proposal_id"]]
