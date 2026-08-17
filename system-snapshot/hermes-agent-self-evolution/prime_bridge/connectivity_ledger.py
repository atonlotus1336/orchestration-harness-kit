"""Append-only desired and observed connectivity state."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prime_bridge.connectivity_policy import AXES, ConnectivityPolicy, ConnectivityPolicyError
from prime_bridge.job_ledger import validate_safe


class ConnectivityLedgerError(RuntimeError):
    pass


STATUSES = {"PASS", "FAIL", "DEFERRED"}
FORBIDDEN_OBSERVED_KEY = re.compile(
    r"(?:authorization|cookie|headers?|environment|api.?key|password|secret|token|credential)",
    re.IGNORECASE,
)


def _utc(value: str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ConnectivityLedgerError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


class ConnectivityLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS desired_snapshots (
                    snapshot_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL, record_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL,
                    machine_role TEXT NOT NULL, axis TEXT NOT NULL,
                    status TEXT NOT NULL, observed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS observation_latest
                    ON observations(resource_id,machine_role,axis,observed_at DESC);
                CREATE TABLE IF NOT EXISTS repair_events (
                    event_id TEXT PRIMARY KEY, envelope TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def snapshot(self, policy: ConnectivityPolicy) -> dict[str, Any]:
        now = _utc().isoformat()
        record = {
            "schema_version": "1.0", "snapshot_id": uuid.uuid4().hex,
            "policy_sha256": policy.source_sha256, "created_at": now,
            "resources": policy.resources, "authority_machine": policy.authority_machine,
        }
        validate_safe(record, max_bytes=65_536)
        with self._connect() as db:
            db.execute(
                "INSERT INTO desired_snapshots VALUES(?,?,?,?)",
                (record["snapshot_id"], now, policy.source_sha256, json.dumps(record, sort_keys=True, separators=(",", ":"))),
            )
        return record

    def ensure_snapshot(self, policy: ConnectivityPolicy) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM desired_snapshots ORDER BY created_at DESC,snapshot_id DESC LIMIT 1",
            ).fetchone()
        if row is not None:
            latest = json.loads(row[0])
            if latest["policy_sha256"] == policy.source_sha256:
                return latest
        return self.snapshot(policy)

    def observe(
        self, policy: ConnectivityPolicy, *, resource_id: str, machine_name: str,
        source: str, software_version: str, axis: str, status: str,
        observed: dict[str, Any], observed_at: str | None = None,
    ) -> dict[str, Any]:
        if resource_id not in policy.resources or axis not in AXES or status not in STATUSES:
            raise ConnectivityLedgerError("invalid observation classification")
        try:
            role = policy.machine_role(machine_name)
        except ConnectivityPolicyError as exc:
            raise ConnectivityLedgerError("unknown machine identity") from exc
        if not policy.accepts_source(role, source):
            raise ConnectivityLedgerError("observation source is not authorized for machine")
        if not isinstance(software_version, str) or not software_version or len(software_version) > 128:
            raise ConnectivityLedgerError("software version is required")
        if not isinstance(observed, dict):
            raise ConnectivityLedgerError("observed value must be an object")
        def forbidden_key(item: Any) -> bool:
            if isinstance(item, dict):
                return any(FORBIDDEN_OBSERVED_KEY.search(str(key)) or forbidden_key(child) for key, child in item.items())
            if isinstance(item, list):
                return any(forbidden_key(child) for child in item)
            return False
        if forbidden_key(observed):
            raise ConnectivityLedgerError("secret-shaped observation metadata is forbidden")
        validate_safe(observed, max_bytes=8_192)
        timestamp = _utc(observed_at)
        if timestamp > _utc() + timedelta(seconds=60):
            raise ConnectivityLedgerError("observation timestamp is in the future")
        expiry = timestamp + timedelta(seconds=policy.freshness_seconds[axis])
        content = json.dumps(observed, sort_keys=True, separators=(",", ":"))
        record = {
            "schema_version": "1.0", "observation_id": uuid.uuid4().hex,
            "resource_id": resource_id, "machine_role": role,
            "machine_name": machine_name, "source": source,
            "software_version": software_version, "axis": axis, "status": status,
            "observed": observed, "observed_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "observed_at": timestamp.isoformat(), "expires_at": expiry.isoformat(),
        }
        validate_safe(record, max_bytes=16_384)
        with self._connect() as db:
            db.execute(
                "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)",
                (record["observation_id"], resource_id, role, axis, status,
                 record["observed_at"], record["expires_at"], json.dumps(record, sort_keys=True, separators=(",", ":"))),
            )
        return record

    def latest(self, resource_id: str, machine_role: str, axis: str, *, now: str | None = None) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM observations WHERE resource_id=? AND machine_role=? AND axis=? ORDER BY observed_at DESC,observation_id DESC LIMIT 1",
                (resource_id, machine_role, axis),
            ).fetchone()
        if row is None:
            return None
        record = json.loads(row[0])
        record["fresh"] = _utc(record["expires_at"]) > _utc(now)
        return record

    def record_repair(self, envelope: str, status: str, data: dict[str, Any]) -> dict[str, Any]:
        validate_safe(data, max_bytes=16_384)
        record = {
            "schema_version": "1.0", "event_id": uuid.uuid4().hex,
            "envelope": envelope, "status": status,
            "created_at": _utc().isoformat(), "data": data,
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO repair_events VALUES(?,?,?,?,?)",
                (record["event_id"], envelope, status, record["created_at"], json.dumps(record, sort_keys=True, separators=(",", ":"))),
            )
        return record

    def repairs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT record_json FROM repair_events ORDER BY created_at,event_id")]
