"""Hermes-owned boundary for normalized topology observations."""

from __future__ import annotations

from typing import Any

from prime_bridge.connectivity_assessor import ConnectivityAssessor
from prime_bridge.connectivity_ledger import ConnectivityLedger
from prime_bridge.connectivity_policy import ConnectivityPolicy


class ConnectivityServiceError(RuntimeError):
    pass


class ConnectivityService:
    def __init__(self, policy: ConnectivityPolicy, ledger: ConnectivityLedger):
        self.policy = policy
        self.ledger = ledger
        self.desired_snapshot = ledger.ensure_snapshot(policy)
        self.assessor = ConnectivityAssessor(policy, ledger)

    def record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "resource_id", "machine_name", "source", "software_version",
            "axis", "status", "observed", "observed_at",
        }
        if set(arguments) - allowed or not allowed.difference({"observed_at"}).issubset(arguments):
            raise ConnectivityServiceError("observation fields are not closed")
        return self.ledger.observe(
            self.policy,
            resource_id=arguments["resource_id"], machine_name=arguments["machine_name"],
            source=arguments["source"], software_version=arguments["software_version"],
            axis=arguments["axis"], status=arguments["status"], observed=arguments["observed"],
            observed_at=arguments.get("observed_at"),
        )

    def status(self) -> dict[str, Any]:
        return self.assessor.assess_all()

    def assess_change(self, change: str) -> dict[str, str]:
        return self.assessor.classify_change(change)
