"""Desired-versus-observed topology assessment with independent axes."""

from __future__ import annotations

from typing import Any

from prime_bridge.connectivity_ledger import ConnectivityLedger
from prime_bridge.connectivity_policy import ConnectivityPolicy


def _matches(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    return all(key in observed and type(observed[key]) is type(value) and observed[key] == value for key, value in expected.items())


class ConnectivityAssessor:
    def __init__(self, policy: ConnectivityPolicy, ledger: ConnectivityLedger):
        self.policy = policy
        self.ledger = ledger

    def assess_resource(self, resource_id: str, *, now: str | None = None) -> dict[str, Any]:
        if resource_id not in self.policy.resources:
            raise ValueError("unknown connectivity resource")
        resource = self.policy.resources[resource_id]
        axes: list[dict[str, Any]] = []
        hard_drift = False
        deferred = False
        for requirement in resource["required_axes"]:
            machine = requirement["machine"]
            axis = requirement["axis"]
            latest = self.ledger.latest(resource_id, machine, axis, now=now)
            item = {"machine": machine, "axis": axis, "expected": requirement["expected"]}
            if latest is None:
                item.update({"status": "MISSING", "fresh": False})
                deferred |= machine == "thin-client"
                hard_drift |= machine != "thin-client"
            elif not latest["fresh"]:
                item.update({"status": "STALE", "fresh": False, "observation_id": latest["observation_id"]})
                deferred |= machine == "thin-client"
                hard_drift |= machine != "thin-client"
            elif latest["status"] == "DEFERRED":
                item.update({"status": "DEFERRED", "fresh": True, "observation_id": latest["observation_id"]})
                deferred = True
            elif latest["status"] != "PASS" or not _matches(requirement["expected"], latest["observed"]):
                item.update({"status": "DRIFT", "fresh": True, "observation_id": latest["observation_id"]})
                hard_drift = True
            else:
                item.update({"status": "PASS", "fresh": True, "observation_id": latest["observation_id"]})
            axes.append(item)
        if hard_drift:
            action = resource["drift_action"]
            status = "REPAIRABLE" if action.startswith("repair:") else "APPROVAL_REQUIRED"
        elif deferred:
            action, status = "wait_for_remote_evidence", "DEFERRED"
        else:
            action, status = "none", "HEALTHY"
        return {
            "resource_id": resource_id, "owner": resource["owner"],
            "status": status, "action": action, "axes": axes,
        }

    def assess_all(self, *, now: str | None = None) -> dict[str, Any]:
        resources = [self.assess_resource(resource_id, now=now) for resource_id in self.policy.resources]
        counts = {status: sum(item["status"] == status for item in resources) for status in (
            "HEALTHY", "REPAIRABLE", "DEFERRED", "APPROVAL_REQUIRED",
        )}
        return {
            "authority_machine": self.policy.authority_machine,
            "desktop_role": "independent-verifier",
            "resources": resources, "counts": counts,
        }

    def classify_change(self, change: str) -> dict[str, str]:
        if change in self.policy.approval_required_changes:
            return {"change": change, "decision": "APPROVAL_REQUIRED"}
        if change == "gateway_host_binding":
            return {"change": change, "decision": "REPAIR_ENVELOPE", "envelope": "gateway-host-binding"}
        return {"change": change, "decision": "REJECTED"}
