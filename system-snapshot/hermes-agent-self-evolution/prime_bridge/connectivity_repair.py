"""Exact, authority-bound connectivity repair with rollback."""

from __future__ import annotations

import hashlib
import re
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prime_bridge.config_transaction import ConfigTransaction
from prime_bridge.connectivity_assessor import ConnectivityAssessor
from prime_bridge.connectivity_ledger import ConnectivityLedger
from prime_bridge.connectivity_policy import ConnectivityPolicy, ConnectivityPolicyError


class ConnectivityRepairError(RuntimeError):
    pass


Probe = Callable[[Path], dict[str, Any]]
Restart = Callable[[tuple[str, ...]], bool]


class ConnectivityRepairEngine:
    def __init__(
        self, policy: ConnectivityPolicy, ledger: ConnectivityLedger,
        *, config_root: Path, rollback_root: Path, machine_name: str | None = None,
    ):
        self.policy = policy
        self.ledger = ledger
        self.assessor = ConnectivityAssessor(policy, ledger)
        self.machine_name = machine_name or socket.gethostname()
        self.transaction = ConfigTransaction(policy, config_root=config_root, rollback_root=rollback_root)

    @staticmethod
    def _candidate(before: bytes, key: str, allowed_before: list[str], desired: str) -> bytes:
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConnectivityRepairError("configuration is not UTF-8") from exc
        pattern = re.compile(rf"^(?P<prefix>{re.escape(key)}=)(?P<value>[^\r\n]*)$", re.MULTILINE)
        matches = list(pattern.finditer(text))
        if len(matches) != 1 or matches[0].group("value") not in allowed_before:
            raise ConnectivityRepairError("configuration key is missing, duplicated, or outside the envelope")
        start, end = matches[0].span("value")
        return (text[:start] + desired + text[end:]).encode("utf-8")

    @staticmethod
    def _probe_status(value: dict[str, Any]) -> str:
        status = value.get("status")
        observed = value.get("observed")
        if status not in {"PASS", "FAIL", "DEFERRED"} or not isinstance(observed, dict):
            raise ConnectivityRepairError("verifier result is not normalized")
        return status

    def repair_gateway_binding(
        self, *, authority_name: str, restart: Restart,
        backend_probe: Probe, desktop_probe: Probe, rollback_id: str,
    ) -> dict[str, Any]:
        try:
            role = self.policy.machine_role(authority_name)
            actual_role = self.policy.machine_role(self.machine_name)
        except ConnectivityPolicyError as exc:
            raise ConnectivityRepairError("repair authority is unknown") from exc
        if role != self.policy.authority_machine or actual_role != self.policy.authority_machine:
            raise ConnectivityRepairError("repair may run only on the NUCBOX authority")
        envelope = self.policy.envelope("gateway-host-binding")
        if envelope is None:
            raise ConnectivityRepairError("gateway repair envelope is unavailable")
        target = (self.transaction.config_root / envelope["target"]).resolve()
        if not target.is_relative_to(self.transaction.config_root) or not target.is_file():
            raise ConnectivityRepairError("gateway configuration target is unavailable")
        before = target.read_bytes()
        candidate = self._candidate(before, envelope["key"], envelope["allowed_before"], envelope["desired"])
        results: dict[str, dict[str, Any]] = {}
        runtime_restarted = False

        def verify_backend(path: Path) -> bool:
            nonlocal runtime_restarted
            if not restart(tuple(envelope["restart_command"])):
                raise ConnectivityRepairError("exact restart command failed")
            runtime_restarted = True
            results["backend"] = backend_probe(path)
            return self._probe_status(results["backend"]) == "PASS"

        def verify_desktop(path: Path) -> bool:
            results["desktop"] = desktop_probe(path)
            return self._probe_status(results["desktop"]) == "PASS"

        receipt = self.transaction.apply(
            envelope_name="gateway-host-binding",
            expected_before_sha256=hashlib.sha256(before).hexdigest(),
            new_bytes=candidate, rollback_id=rollback_id,
            local_verify=verify_backend, remote_verify=verify_desktop,
        )
        rollback_runtime_status = "NOT_REQUIRED"
        if receipt["status"] == "rolled_back" and runtime_restarted:
            rollback_runtime_status = "PASS" if restart(tuple(envelope["restart_command"])) else "FAIL"
            if rollback_runtime_status == "FAIL":
                event = self.ledger.record_repair("gateway-host-binding", "ROLLBACK_FAILED", {
                    "authority_machine": role, "rollback_id": rollback_id,
                    "transaction_status": receipt["status"], "rollback_runtime_status": "FAIL",
                    "before_sha256": receipt["before_sha256"], "after_sha256": receipt["after_sha256"],
                })
                raise ConnectivityRepairError(f"configuration bytes restored but runtime rollback failed: {event['event_id']}")
        if "backend" in results:
            self.ledger.observe(
                self.policy, resource_id="gateway-8642", machine_name=authority_name,
                source="repair-verifier", software_version="phase6-repair-v1", axis="backend",
                status=results["backend"]["status"], observed=results["backend"]["observed"],
            )
        if "desktop" in results:
            desktop_name = self.policy.machines["thin-client"]["aliases"][0]
            self.ledger.observe(
                self.policy, resource_id="gateway-8642", machine_name=desktop_name,
                source="repair-verifier", software_version="phase6-repair-v1", axis="client",
                status=results["desktop"]["status"], observed=results["desktop"]["observed"],
            )
        if receipt["status"] == "applied":
            status = "VERIFIED"
        elif results.get("desktop", {}).get("status") == "DEFERRED":
            status = "DEFERRED"
        else:
            status = "ROLLED_BACK"
        event = self.ledger.record_repair("gateway-host-binding", status, {
            "authority_machine": role,
            "before_sha256": receipt["before_sha256"],
            "candidate_sha256": receipt["candidate_sha256"],
            "after_sha256": receipt["after_sha256"],
            "rollback_id": rollback_id,
            "transaction_status": receipt["status"],
            "backend_status": results.get("backend", {}).get("status", "NOT_RUN"),
            "desktop_status": results.get("desktop", {}).get("status", "NOT_RUN"),
            "rollback_runtime_status": rollback_runtime_status,
        })
        learning_inputs = []
        if status == "VERIFIED":
            learning_inputs = [{
                "lane": "fact", "scope": "global", "subject": "connectivity.gateway-8642-binding",
                "content": "The workspace gateway is verified on 0.0.0.0:8642 from the NUCBOX backend and __OWNER_HANDLE__ desktop client.",
                "evidence": {"repair_event_id": event["event_id"], "after_sha256": receipt["after_sha256"]},
            }]
        return {"status": status, "receipt": receipt, "repair_event": event, "learning_inputs": learning_inputs}

    def assess_change(self, change: str) -> dict[str, str]:
        return self.assessor.classify_change(change)
