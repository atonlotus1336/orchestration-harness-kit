import json
from pathlib import Path

import pytest

from prime_bridge.config_transaction import ConfigTransaction, ConfigTransactionError
from prime_bridge.learning_ledger import LearningLedger
from prime_bridge.promotion_engine import PromotionEngine
from prime_bridge.promotion_policy import PromotionPolicy

ROOT = Path(__file__).resolve().parents[2]


def policy(tmp_path, *, remote=True):
    value = json.loads((ROOT / "config/prime-learning-policy.json").read_text())
    value["configuration_envelopes"] = [{
        "name": "gateway-host", "target": "workspace/.env", "remote_required": remote,
    }]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return PromotionPolicy.load(path)


def setup(tmp_path, *, remote=True):
    config = tmp_path / "config"
    target = config / "workspace/.env"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"API_SERVER_HOST=127.0.0.1\n")
    tx = ConfigTransaction(policy(tmp_path, remote=remote), config_root=config, rollback_root=tmp_path / "rollback")
    return tx, target


def test_exact_envelope_applies_after_local_and_remote_verification(tmp_path):
    tx, target = setup(tmp_path)
    receipt = tx.apply(
        envelope_name="gateway-host", expected_before_sha256=tx.digest(target.read_bytes()),
        new_bytes=b"API_SERVER_HOST=0.0.0.0\n", rollback_id="a" * 32,
        local_verify=lambda path: path.read_text().strip().endswith("0.0.0.0"),
        remote_verify=lambda _path: True,
    )
    assert receipt["status"] == "applied"
    assert target.read_bytes() == b"API_SERVER_HOST=0.0.0.0\n"
    assert receipt["remote_verified"] is True


@pytest.mark.parametrize("failure", ["local", "remote"])
def test_any_verification_failure_restores_exact_original_bytes(tmp_path, failure):
    tx, target = setup(tmp_path)
    before = target.read_bytes()
    receipt = tx.apply(
        envelope_name="gateway-host", expected_before_sha256=tx.digest(before),
        new_bytes=b"API_SERVER_HOST=0.0.0.0\n", rollback_id=("b" if failure == "local" else "c") * 32,
        local_verify=lambda _path: failure != "local",
        remote_verify=lambda _path: failure != "remote",
    )
    assert receipt["status"] == "rolled_back"
    assert target.read_bytes() == before
    backup = tmp_path / "rollback" / receipt["rollback_id"] / receipt["backup"]
    assert backup.read_bytes() == before


def test_verifier_cannot_mutate_candidate_and_still_pass(tmp_path):
    tx, target = setup(tmp_path)
    before = target.read_bytes()

    def mutating_remote(path):
        path.write_bytes(b"API_SERVER_HOST=attacker-controlled\n")
        return True

    receipt = tx.apply(
        envelope_name="gateway-host",
        expected_before_sha256=tx.digest(before),
        new_bytes=b"API_SERVER_HOST=0.0.0.0\n",
        rollback_id="e" * 32,
        local_verify=lambda _path: True,
        remote_verify=mutating_remote,
    )

    assert receipt["status"] == "rolled_back"
    assert receipt["failure_type"] == "ConfigTransactionError"
    assert target.read_bytes() == before


def test_unknown_envelope_hash_drift_and_missing_remote_verifier_fail_closed(tmp_path):
    tx, target = setup(tmp_path)
    with pytest.raises(ConfigTransactionError, match="not authorized"):
        tx.apply(envelope_name="other", expected_before_sha256=tx.digest(target.read_bytes()), new_bytes=b"x", rollback_id="d" * 32, local_verify=lambda _: True)
    with pytest.raises(ConfigTransactionError, match="drifted"):
        tx.apply(envelope_name="gateway-host", expected_before_sha256="0" * 64, new_bytes=b"x", rollback_id="d" * 32, local_verify=lambda _: True, remote_verify=lambda _: True)
    with pytest.raises(ConfigTransactionError, match="remote verification"):
        tx.apply(envelope_name="gateway-host", expected_before_sha256=tx.digest(target.read_bytes()), new_bytes=b"x", rollback_id="d" * 32, local_verify=lambda _: True)


def test_configuration_proposal_requires_approval_and_records_rollback(tmp_path):
    tx, target = setup(tmp_path)
    ledger = LearningLedger(tmp_path / "learning.sqlite3")
    engine = PromotionEngine(ledger, tx.policy, shared_root=tmp_path / "shared")
    record = ledger.record(
        profile="home", project="__PRIVATE_TOKEN_02__", session_id="session-1", scope="project",
        subject="setup.gateway-host", content="Set the approved gateway host binding.",
        privacy="internal", provenance={"job_id": "a" * 32},
        evidence=[{"kind": "probe", "reference": "gateway", "outcome": "passed"}],
    )
    proposal = ledger.propose(record["record_id"], profile="home", lane="configuration", target_scope="profile")
    proposal = ledger.validate(proposal["proposal_id"], validator="host", mode="deterministic", outcome="passed", evidence_hash="1" * 64)
    assert engine.evaluate(proposal["proposal_id"])["status"] == "approval_required"
    approved = engine.resolve(proposal["proposal_id"], decision="approved", approver="hermes-home")
    assert approved["status"] == "approved"
    before = target.read_bytes()
    result = engine.apply_configuration(
        proposal["proposal_id"], transaction=tx, envelope_name="gateway-host",
        expected_before_sha256=tx.digest(before), new_bytes=b"API_SERVER_HOST=0.0.0.0\n",
        local_verify=lambda _: True, remote_verify=lambda _: False,
    )
    assert result["status"] == "rolled_back" and target.read_bytes() == before
    assert ledger.events(proposal["proposal_id"])[-1]["kind"] == "configuration_rolled_back"
