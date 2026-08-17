"""Bounded staged maintenance orchestration; live update adapters remain explicit."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from prime_bridge.maintenance_ledger import COMPONENTS, HALT_CLASSES, MaintenanceLedger


class MaintenancePolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateResult:
    updated: bool
    component_passed: bool
    stack_passed: bool
    commit_passed: bool
    rollback_passed: bool = True
    halt_class: str | None = None
    evidence: dict[str, Any] | None = None


class MaintenanceAdapter(Protocol):
    def snapshot(self, component: str) -> dict[str, Any]: ...
    def update_candidate(self, component: str) -> CandidateResult: ...
    def restore(self, component: str, snapshot: dict[str, Any]) -> bool: ...


def load_policy(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "schedule", "components", "outcomes", "halt_classes", "prohibited_mutations", "automatic_push", "retention_runs"}
    if set(value) != required or value["components"] != list(COMPONENTS):
        raise MaintenancePolicyError("maintenance policy is not closed")
    if value["schedule"] != {"day": "Wednesday", "time": "14:00", "timezone": "America/New_York", "enabled_by_default": False}:
        raise MaintenancePolicyError("unexpected maintenance schedule")
    if value["automatic_push"] is not False:
        raise MaintenancePolicyError("automatic remote push is forbidden")
    return value


class MaintenanceController:
    def __init__(self, *, policy_path: Path, ledger: MaintenanceLedger, adapter: MaintenanceAdapter):
        self.policy = load_policy(policy_path)
        self.ledger, self.adapter = ledger, adapter

    def run(self, *, run_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        if not dry_run:
            raise MaintenancePolicyError("live maintenance requires a separately approved adapter")
        run_id = run_id or uuid.uuid4().hex
        outcomes: list[dict[str, Any]] = []
        halted = False
        for component in COMPONENTS:
            snapshot = self.ledger.snapshot(run_id, component, self.adapter.snapshot(component))
            result = self.adapter.update_candidate(component)
            evidence = dict(result.evidence or {})
            evidence["restore_id"] = snapshot["restore_id"]
            if result.updated and result.component_passed and result.stack_passed and result.commit_passed:
                outcomes.append(self.ledger.record(run_id, component, outcome="PROMOTED", data=evidence))
                continue
            if result.halt_class in HALT_CLASSES or not result.rollback_passed or not self.adapter.restore(component, snapshot):
                halt = result.halt_class or "ROLLBACK_UNVERIFIED"
                outcomes.append(self.ledger.record(run_id, component, halt_class=halt, data=evidence))
                halted = True
                break
            outcomes.append(self.ledger.record(run_id, component, outcome="FAILED_RESTORED", data=evidence))
        if not halted:
            done = {item["component"] for item in outcomes}
            for component in COMPONENTS:
                if component not in done:
                    outcomes.append(self.ledger.record(run_id, component, outcome="SKIPPED", data={"reason": "not-reached"}))
        return {"run_id": run_id, "dry_run": True, "halted": halted, "events": outcomes}
