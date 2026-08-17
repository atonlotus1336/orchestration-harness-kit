"""Transactional configuration application with exact rollback semantics."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from prime_bridge.promotion_policy import PromotionPolicy


class ConfigTransactionError(RuntimeError):
    pass


Verifier = Callable[[Path], bool]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ConfigTransaction:
    def __init__(self, policy: PromotionPolicy, *, config_root: Path, rollback_root: Path):
        self.policy = policy
        self.config_root = config_root.resolve(strict=True)
        self.rollback_root = rollback_root.resolve()

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def apply(
        self, *, envelope_name: str, expected_before_sha256: str,
        new_bytes: bytes, rollback_id: str, local_verify: Verifier,
        remote_verify: Verifier | None = None,
    ) -> dict[str, object]:
        envelope = self.policy.envelope(envelope_name)
        if envelope is None:
            raise ConfigTransactionError("configuration envelope is not authorized")
        target = (self.config_root / envelope["target"]).resolve()
        if not target.is_relative_to(self.config_root) or not target.is_file():
            raise ConfigTransactionError("configuration target is unavailable")
        before = target.read_bytes()
        if self.digest(before) != expected_before_sha256:
            raise ConfigTransactionError("configuration before hash drifted")
        if not isinstance(new_bytes, bytes) or not new_bytes:
            raise ConfigTransactionError("configuration candidate is empty")
        if envelope["remote_required"] and remote_verify is None:
            raise ConfigTransactionError("remote verification is required")
        backup = (self.rollback_root / rollback_id / envelope["target"]).resolve()
        if not backup.is_relative_to(self.rollback_root):
            raise ConfigTransactionError("rollback target escaped its root")
        _atomic_write(backup, before)
        status, failure = "applied", None
        try:
            _atomic_write(target, new_bytes)
            if not local_verify(target):
                raise ConfigTransactionError("local verification failed")
            if remote_verify is not None and not remote_verify(target):
                raise ConfigTransactionError("remote verification failed")
            if target.read_bytes() != new_bytes:
                raise ConfigTransactionError("configuration candidate changed during verification")
        except Exception as exc:
            _atomic_write(target, before)
            status, failure = "rolled_back", type(exc).__name__
            if target.read_bytes() != before:
                raise ConfigTransactionError("configuration rollback failed") from exc
        receipt = {
            "schema_version": "1.0", "envelope": envelope_name,
            "target": envelope["target"], "status": status,
            "before_sha256": self.digest(before), "candidate_sha256": self.digest(new_bytes),
            "after_sha256": self.digest(target.read_bytes()),
            "rollback_id": rollback_id,
            "backup": str(backup.relative_to(self.rollback_root / rollback_id)),
            "local_verified": status == "applied",
            "remote_required": envelope["remote_required"],
            "remote_verified": status == "applied" and remote_verify is not None,
            "failure_type": failure,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt_path = self.rollback_root / rollback_id / "receipt.json"
        _atomic_write(receipt_path, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
        return receipt
