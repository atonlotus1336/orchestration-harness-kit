from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prime_bridge.capability_ledger import CapabilityLedger, CapabilityLedgerError
from prime_bridge.capability_policy import CapabilityPolicy, CapabilityPolicyError

ROOT = Path(__file__).resolve().parents[2]


def test_policy_is_closed_and_public_catalog_hides_mcp_targets():
    policy = CapabilityPolicy.load(ROOT / "config/prime-capability-policy.json")
    assert policy.decision("memory.search").classification == "retrieval"
    assert policy.decision("wordpress.update").approval == "required"
    with pytest.raises(CapabilityPolicyError, match="denied"):
        policy.decision("shell.exec")
    catalog = policy.public_catalog()
    assert "memory.search" in catalog
    assert "memoryweaver-mcp" not in str(catalog)


def test_arguments_are_canonical_secret_free_and_bounded():
    policy = CapabilityPolicy.load(ROOT / "config/prime-capability-policy.json")
    canonical = policy.canonical_arguments({"query": "deployment checklist", "limit": 3})
    assert len(canonical.digest) == 64
    assert canonical.keys == ("limit", "query")
    with pytest.raises(CapabilityPolicyError, match="secret"):
        policy.canonical_arguments({"api_key": "not-persisted"})
    with pytest.raises(CapabilityPolicyError, match="bounded"):
        policy.canonical_arguments({"query": "x" * 70_000})


def test_approval_is_profile_job_argument_bound_expiring_and_single_use(tmp_path):
    policy = CapabilityPolicy.load(ROOT / "config/prime-capability-policy.json")
    ledger = CapabilityLedger(tmp_path / "capabilities.sqlite3")
    args = policy.canonical_arguments({"post_id": 42, "title": "Draft"})
    request = ledger.create(
        job_id="a" * 32, profile="home", alias="wordpress.update",
        decision=policy.decision("wordpress.update"), arguments=args,
    )
    assert request["status"] == "pending"
    assert request["argument_keys"] == ["post_id", "title"]
    assert "Draft" not in str(request)
    with pytest.raises(CapabilityLedgerError, match="profile"):
        ledger.resolve(request["request_id"], profile="other", decision="approved", approver="operator", operator_nonce=request["operator_nonce"])
    approved = ledger.resolve(request["request_id"], profile="home", decision="approved", approver="operator", operator_nonce=request["operator_nonce"])
    assert approved["approval_provenance"]["approver"] == "operator"
    ledger.authorize(
        request["request_id"], profile="home", job_id="a" * 32,
        argument_hash=args.digest,
    )
    with pytest.raises(CapabilityLedgerError, match="single-use"):
        ledger.authorize(request["request_id"], profile="home", job_id="a" * 32, argument_hash=args.digest)

    expired = ledger.create(
        job_id="b" * 32, profile="home", alias="wordpress.update",
        decision=policy.decision("wordpress.update"), arguments=args,
        now=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    ledger.resolve(expired["request_id"], profile="home", decision="approved", approver="operator", operator_nonce=expired["operator_nonce"])
    with pytest.raises(CapabilityLedgerError, match="expired"):
        ledger.authorize(expired["request_id"], profile="home", job_id="b" * 32, argument_hash=args.digest)


def test_auto_and_denied_requests_are_audited_without_argument_values(tmp_path):
    policy = CapabilityPolicy.load(ROOT / "config/prime-capability-policy.json")
    ledger = CapabilityLedger(tmp_path / "capabilities.sqlite3")
    args = policy.canonical_arguments({"query": "private project phrase"})
    request = ledger.create(
        job_id="c" * 32, profile="home", alias="memory.search",
        decision=policy.decision("memory.search"), arguments=args,
    )
    assert request["status"] == "approved"
    assert "private project phrase" not in str(ledger.list(profile="home"))
    ledger.authorize(request["request_id"], profile="home", job_id="c" * 32, argument_hash=args.digest)
    done = ledger.complete(request["request_id"], outcome="ok", result_summary={"items": 2})
    assert done["status"] == "executed"
    assert done["result_summary"] == {"items": 2}
