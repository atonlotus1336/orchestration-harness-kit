#!/usr/bin/env python3
"""Isolated end-to-end smoke for the Phase 5 promotion lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prime_bridge.config_transaction import ConfigTransaction  # noqa: E402
from prime_bridge.learning_ledger import LearningLedger  # noqa: E402
from prime_bridge.promotion_engine import PromotionEngine  # noqa: E402
from prime_bridge.promotion_policy import PromotionPolicy  # noqa: E402

SOURCES = (
    "config/prime-learning-policy.json", "config/prime-capability-policy.json",
    "prime_bridge/learning_ledger.py", "prime_bridge/promotion_policy.py",
    "prime_bridge/promotion_engine.py", "prime_bridge/config_transaction.py",
    "prime_bridge/learning_service.py", "prime_bridge/capability_broker.py",
    "prime_bridge/mcp_server.py", "scripts/smoke-prime-learning-promotion.py",
)


def _record(ledger: LearningLedger, subject: str, content: str):
    return ledger.record(
        profile="home", project="phase5-smoke", session_id="phase5-session",
        scope="project", subject=subject, content=content, privacy="internal",
        provenance={"job_id": "5" * 32, "source": "isolated-smoke"},
        evidence=[{"kind": "test", "reference": "phase5-smoke", "outcome": "passed"}],
    )


def _ready(ledger: LearningLedger, record: dict, lane: str, scope: str, *, rank: int = 10, mode: str = "independent"):
    proposal = ledger.propose(record["record_id"], profile="home", lane=lane, target_scope=scope, authority_rank=rank)
    return ledger.validate(
        proposal["proposal_id"], validator="phase5-host-smoke", mode=mode,
        outcome="passed", evidence_hash=hashlib.sha256(record["content"].encode()).hexdigest(),
    )


def run(root: Path) -> dict[str, object]:
    policy_value = json.loads((ROOT / "config/prime-learning-policy.json").read_text())
    policy_value["configuration_envelopes"] = [{
        "name": "smoke-config", "target": "service/config.env", "remote_required": True,
    }]
    policy_path = root / "policy.json"
    policy_path.write_text(json.dumps(policy_value), encoding="utf-8")
    policy = PromotionPolicy.load(policy_path)
    ledger = LearningLedger(root / "learning.sqlite3")
    engine = PromotionEngine(ledger, policy, shared_root=root / "shared")
    cases: dict[str, str] = {}

    fact = _ready(ledger, _record(ledger, "debug.gateway-binding", "Gateway 8642 binds to 0.0.0.0."), "fact", "global")
    cases["auto_fact"] = engine.evaluate(fact["proposal_id"])["status"]

    procedure = _ready(ledger, _record(ledger, "setup.gateway-check", "Run the watchdog then verify /health."), "procedure", "project", mode="deterministic")
    cases["validated_procedure"] = engine.evaluate(procedure["proposal_id"])["status"]

    duplicate = _ready(ledger, ledger.get_record(fact["record_id"]), "fact", "global")
    cases["deduplication"] = engine.evaluate(duplicate["proposal_id"])["status"]

    conflict_record = _record(ledger, "debug.gateway-binding", "Gateway 8642 binds only to loopback.")
    conflict = _ready(ledger, conflict_record, "fact", "global", rank=1)
    cases["authority_conflict"] = engine.evaluate(conflict["proposal_id"])["status"]

    try:
        _record(ledger, "debug.bad", "Use " + "sk-" + "a" * 30 + " for access.")
    except RuntimeError:
        cases["secret_rejection"] = "rejected"
    else:
        cases["secret_rejection"] = "FAILED"

    expiring = ledger.propose(_record(ledger, "debug.expiry", "Temporary observation.")["record_id"], profile="home", lane="fact", target_scope="project")
    ledger.update(expiring["proposal_id"], expected={"proposed"}, status="proposed", changes={
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    }, event="smoke_clock_advanced")
    cases["expiry"] = "expired" if engine.expire(profile="home") == [expiring["proposal_id"]] else "FAILED"

    revoked = engine.revoke(fact["proposal_id"], approver="hermes-home", reason="smoke revocation")
    cases["revocation"] = revoked["status"]

    config_root = root / "config"
    target = config_root / "service/config.env"
    target.parent.mkdir(parents=True)
    before = b"BIND=127.0.0.1\n"
    target.write_bytes(before)
    transaction = ConfigTransaction(policy, config_root=config_root, rollback_root=root / "rollback")
    config_proposal = _ready(ledger, _record(ledger, "setup.config-binding", "Apply the exact approved binding."), "configuration", "profile", mode="deterministic")
    engine.evaluate(config_proposal["proposal_id"])
    engine.resolve(config_proposal["proposal_id"], decision="approved", approver="hermes-home")
    rolled_back = engine.apply_configuration(
        config_proposal["proposal_id"], transaction=transaction, envelope_name="smoke-config",
        expected_before_sha256=transaction.digest(before), new_bytes=b"BIND=0.0.0.0\n",
        local_verify=lambda _: True, remote_verify=lambda _: False,
    )
    cases["configuration_rollback"] = rolled_back["status"] if target.read_bytes() == before else "FAILED"

    expected = {
        "auto_fact": "promoted", "validated_procedure": "promoted",
        "deduplication": "deduplicated", "authority_conflict": "conflict",
        "secret_rejection": "rejected", "expiry": "expired",
        "revocation": "revoked", "configuration_rollback": "rolled_back",
    }
    return {
        "schema_version": "1.0", "environment": "isolated-temporary",
        "status": "PASS" if cases == expected else "FAIL", "cases": cases,
        "proposal_count": len(ledger.list_proposals(profile="home")),
        "event_count": sum(len(ledger.events(item["proposal_id"])) for item in ledger.list_proposals(profile="home")),
        "source_sha256": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in SOURCES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="phase5-learning-smoke-") as temporary:
        result = run(Path(temporary))
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
