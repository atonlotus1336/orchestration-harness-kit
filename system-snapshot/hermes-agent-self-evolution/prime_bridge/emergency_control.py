"""Hash-bound transactional emergency disable for the central Prime policy."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from prime_bridge.config_transaction import _atomic_write
from prime_bridge.profile_policy import PrimeProfilePolicy


class EmergencyControlError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EmergencyController:
    def __init__(self, policy_path: Path, backup_root: Path):
        self.policy_path = policy_path.resolve(strict=True)
        self.backup_root = backup_root.resolve()

    def plan(self) -> dict[str, object]:
        before = self.policy_path.read_bytes()
        policy = PrimeProfilePolicy.load(self.policy_path)
        value = json.loads(before)
        value["enabled"] = False
        candidate = (json.dumps(value, indent=2, sort_keys=False) + "\n").encode()
        return {
            "status": "ALREADY_DISABLED" if not policy.enabled else "READY",
            "before_sha256": digest(before), "candidate_sha256": digest(candidate),
            "candidate": candidate, "confirmation": "DISABLE-PRIME",
            "native_fallback": True, "activation": "restart-boundary",
        }

    def apply(self, *, expected_before_sha256: str, rollback_id: str, confirmation: str, verify=None) -> dict[str, object]:
        if confirmation != "DISABLE-PRIME":
            raise EmergencyControlError("exact emergency confirmation is required")
        if not rollback_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in rollback_id):
            raise EmergencyControlError("invalid rollback identifier")
        before = self.policy_path.read_bytes()
        if digest(before) != expected_before_sha256:
            raise EmergencyControlError("emergency policy before hash drifted")
        plan = self.plan()
        if plan["status"] != "READY":
            raise EmergencyControlError("Prime is already disabled")
        backup = (self.backup_root / rollback_id / "prime-profile-policy.json").resolve()
        if not backup.is_relative_to(self.backup_root):
            raise EmergencyControlError("backup escaped rollback root")
        _atomic_write(backup, before)
        status, failure = "disabled", None
        try:
            _atomic_write(self.policy_path, plan["candidate"])
            loaded = PrimeProfilePolicy.load(self.policy_path)
            if loaded.enabled or not loaded.native_fallback or loaded.activation != "restart-boundary":
                raise EmergencyControlError("disabled policy verification failed")
            if verify is not None and not verify(self.policy_path):
                raise EmergencyControlError("external emergency verification failed")
            if digest(self.policy_path.read_bytes()) != plan["candidate_sha256"]:
                raise EmergencyControlError("emergency policy changed during verification")
        except Exception as exc:
            _atomic_write(self.policy_path, before)
            status, failure = "rolled_back", type(exc).__name__
            if self.policy_path.read_bytes() != before:
                raise EmergencyControlError("emergency rollback failed") from exc
        receipt = {
            "schema_version": "1.0", "status": status,
            "before_sha256": digest(before), "candidate_sha256": plan["candidate_sha256"],
            "after_sha256": digest(self.policy_path.read_bytes()), "rollback_id": rollback_id,
            "backup": str(backup.relative_to(self.backup_root / rollback_id)),
            "failure_type": failure, "native_hermes": "AVAILABLE",
            "activation": "restart-boundary", "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt_path = self.backup_root / rollback_id / "receipt.json"
        _atomic_write(receipt_path, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
        return receipt

    def rollback(self, *, rollback_id: str, expected_current_sha256: str) -> dict[str, str]:
        backup = (self.backup_root / rollback_id / "prime-profile-policy.json").resolve()
        if not backup.is_relative_to(self.backup_root) or not backup.is_file():
            raise EmergencyControlError("emergency backup is unavailable")
        if digest(self.policy_path.read_bytes()) != expected_current_sha256:
            raise EmergencyControlError("emergency rollback current hash drifted")
        original = backup.read_bytes()
        _atomic_write(self.policy_path, original)
        policy = PrimeProfilePolicy.load(self.policy_path)
        if not policy.enabled or not policy.native_fallback:
            raise EmergencyControlError("emergency rollback verification failed")
        return {"status": "restored", "restored_sha256": digest(original), "native_hermes": "AVAILABLE"}
