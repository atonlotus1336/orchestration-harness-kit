
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prime_bridge.adaptive_review.herdr_transport import (
    HerdrReviewCapabilityService,
)
from prime_bridge.adaptive_review.transport import BudgetViolation
from prime_bridge.capability_broker import CapabilityBroker, CapabilityBrokerError
from prime_bridge.capability_ledger import CapabilityLedger, CapabilityLedgerError
from prime_bridge.capability_policy import CapabilityPolicy
from prime_bridge.orchestration_switch import (
    OrchestrationSwitch, operation_permitted,
)

ROOT = Path(__file__).resolve().parents[2]


def arguments(**overrides):
    value = {
        "harness": "codex", "receipt_id": "receipt-1",
        "attempt_id": "attempt-1", "reviewer_id": "correctness",
        "request_sha256": "a" * 64, "s_level": "S1",
        "s4_confirmed": False,
    }
    value.update(overrides)
    return value


def capability_context(**overrides):
    value = {
        "harness": "codex",
        "request_id": "request-1",
        "capability_receipt": "b" * 64,
    }
    value.update(overrides)
    return value


def test_shipped_switch_and_capability_policy_are_off_and_approval_gated(tmp_path):
    assert operation_permitted("herdr-review", "codex", "start", tmp_path / "missing.json") is False
    policy = json.loads((ROOT / "config/prime-capability-policy.json").read_text())
    alias = policy["aliases"]["herdr.reviewer.start"]
    assert alias["classification"] == "action"
    assert alias["approval"] == "required"
    assert alias["server"] == "herdr-reviewer"


def test_supervised_start_requires_exact_receipt_binding(tmp_path):
    switch = OrchestrationSwitch(tmp_path)
    switch.set("herdr-review", "supervised", ("codex",))
    calls = []
    service = HerdrReviewCapabilityService(
        switch_path=switch.path,
        starter=lambda value: calls.append(value) or {"status": "STARTED"},
    )
    with pytest.raises(BudgetViolation, match="binding"):
        asyncio.run(service.call(
            "start", arguments(),
            capability_context(capability_receipt="bad"),
        ))
    result = asyncio.run(service.call(
        "start", arguments(),
        capability_context(),
    ))
    assert result == {"status": "STARTED"}
    assert len(calls) == 1

def test_s4_confirmation_and_disconnected_starter_fail_closed(tmp_path):
    switch = OrchestrationSwitch(tmp_path)
    switch.set("herdr-review", "full", ("codex",))
    service = HerdrReviewCapabilityService(switch_path=switch.path, starter=lambda value: {})
    with pytest.raises(BudgetViolation, match="S4 confirmation"):
        asyncio.run(service.call(
            "start", arguments(s_level="S4"),
            capability_context(),
        ))
    disconnected = HerdrReviewCapabilityService(switch_path=switch.path)
    with pytest.raises(BudgetViolation, match="not connected"):
        asyncio.run(disconnected.call(
            "start", arguments(),
            capability_context(),
        ))

def test_disable_blocks_new_start_and_ledger_is_expiring_scope_bound_single_use(tmp_path):
    switch = OrchestrationSwitch(tmp_path / "switch")
    switch.set("herdr-review", "supervised", ("codex",))
    calls = []
    service = HerdrReviewCapabilityService(
        switch_path=switch.path,
        starter=lambda value: calls.append(value) or {"status": "STARTED"},
    )
    assert asyncio.run(service.call("start", arguments(), capability_context())) == {
        "status": "STARTED"
    }
    switch.set("herdr-review", "off", ("codex",))
    with pytest.raises(BudgetViolation, match="off"):
        asyncio.run(service.call("start", arguments(attempt_id="attempt-2"), capability_context()))
    assert len(calls) == 1

    policy = CapabilityPolicy.load(ROOT / "config/prime-capability-policy.json")
    ledger = CapabilityLedger(tmp_path / "capabilities.sqlite3")
    canonical = policy.canonical_arguments(arguments())
    request = ledger.create(
        job_id="a" * 32, profile="codex", alias="herdr.reviewer.start",
        decision=policy.decision("herdr.reviewer.start"), arguments=canonical,
    )
    approved = ledger.resolve(
        request["request_id"], profile="codex", decision="approved",
        approver="operator", operator_nonce=request["operator_nonce"],
    )
    ledger.authorize(
        approved["request_id"], profile="codex", job_id="a" * 32,
        argument_hash=canonical.digest,
    )
    with pytest.raises(CapabilityLedgerError, match="single-use"):
        ledger.authorize(
            approved["request_id"], profile="codex", job_id="a" * 32,
            argument_hash=canonical.digest,
        )

    expired = ledger.create(
        job_id="b" * 32, profile="codex", alias="herdr.reviewer.start",
        decision=policy.decision("herdr.reviewer.start"), arguments=canonical,
        now=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    ledger.resolve(
        expired["request_id"], profile="codex", decision="approved",
        approver="operator", operator_nonce=expired["operator_nonce"],
    )
    with pytest.raises(CapabilityLedgerError, match="expired"):
        ledger.authorize(
            expired["request_id"], profile="codex", job_id="b" * 32,
            argument_hash=canonical.digest,
        )

def test_broker_supplies_authorized_context_and_prevents_replay(tmp_path):
    class NoExternalAdapter:
        def configured(self):
            return ()
        async def call(self, server, tool, arguments):
            raise AssertionError("external adapter must not be used")

    switch = OrchestrationSwitch(tmp_path / "switch-broker")
    switch.set("herdr-review", "supervised", ("codex",))
    calls = []
    service = HerdrReviewCapabilityService(
        switch_path=switch.path,
        starter=lambda value: calls.append(value) or {"status": "STARTED"},
    )
    policy = CapabilityPolicy.load(ROOT / "config/prime-capability-policy.json")
    ledger = CapabilityLedger(tmp_path / "broker-capabilities.sqlite3")
    broker = CapabilityBroker(
        policy, ledger, NoExternalAdapter(),
        internal_handlers={"herdr-reviewer": service.call},
        switch_state_path=switch.path,
    )
    request = {
        "job_id": "c" * 32, "profile": "codex", "harness": "codex",
        "alias": "herdr.reviewer.start", "arguments": arguments(),
    }
    pending = asyncio.run(broker.handle(request))
    assert pending["status"] == "approval_required"
    record = ledger.get(pending["request_id"])
    ledger.resolve(
        record["request_id"], profile="codex", decision="approved",
        approver="operator", operator_nonce=record["operator_nonce"],
    )
    request["request_id"] = record["request_id"]
    completed = asyncio.run(broker.handle(request))
    assert completed["status"] == "ok"
    assert completed["result"] == {"status": "STARTED"}
    assert len(calls) == 1
    with pytest.raises(CapabilityBrokerError):
        asyncio.run(broker.handle(request))
    assert len(calls) == 1
