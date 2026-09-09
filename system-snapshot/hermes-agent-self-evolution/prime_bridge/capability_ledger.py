"""Durable, profile-scoped capability audit and approval state."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prime_bridge.capability_policy import CanonicalArguments, CapabilityDecision
from prime_bridge.job_ledger import validate_safe


class CapabilityLedgerError(RuntimeError):
    pass


IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityLedger:
    def __init__(self, path: Path, *, approval_ttl_seconds: int = 900):
        if approval_ttl_seconds < 1:
            raise CapabilityLedgerError("positive approval TTL required")
        self.path = path
        self.approval_ttl_seconds = approval_ttl_seconds
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS capability_requests (
                    request_id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS capability_scope
                    ON capability_requests(profile,job_id,status,updated_at);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _identity(label: str, value: str) -> str:
        if not isinstance(value, str) or not IDENTITY.fullmatch(value):
            raise CapabilityLedgerError(f"invalid {label}")
        return value

    def create(
        self, *, job_id: str, profile: str, alias: str,
        decision: CapabilityDecision, arguments: CanonicalArguments,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self._identity("profile", profile)
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise CapabilityLedgerError("invalid job")
        instant = now or _now()
        request_id = uuid.uuid4().hex
        status = "approved" if decision.approval == "auto" else "pending"
        expires_at = (instant + timedelta(seconds=self.approval_ttl_seconds)).isoformat()
        record = {
            "schema_version": "1.0", "request_id": request_id,
            "job_id": job_id, "profile": profile, "alias": alias,
            "target": {"server": decision.server, "tool": decision.tool},
            "classification": decision.classification,
            "argument_hash": arguments.digest, "argument_keys": list(arguments.keys),
            "status": status, "created_at": instant.isoformat(),
            "updated_at": instant.isoformat(), "expires_at": expires_at,
            "operator_nonce": uuid.uuid4().hex,
            "approval_provenance": ({"kind": "policy", "approver": "hermes-policy"} if status == "approved" else None),
            "result_summary": None,
        }
        validate_safe(record)
        with self._connect() as db:
            db.execute(
                "INSERT INTO capability_requests VALUES(?,?,?,?,?,?)",
                (request_id, profile, job_id, status, json.dumps(record, sort_keys=True, separators=(",", ":")), instant.isoformat()),
            )
        return record

    def get(self, request_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT record_json FROM capability_requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise CapabilityLedgerError("unknown capability request")
        return json.loads(row[0])

    def list(self, *, profile: str, pending: bool = False) -> list[dict[str, Any]]:
        self._identity("profile", profile)
        query = "SELECT record_json FROM capability_requests WHERE profile=?"
        values: list[Any] = [profile]
        if pending:
            query += " AND status='pending'"
        query += " ORDER BY updated_at,request_id"
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute(query, values)]

    def _replace(self, record: dict[str, Any], *, expected_status: str) -> dict[str, Any]:
        record["updated_at"] = _now().isoformat()
        validate_safe(record)
        with self._connect() as db:
            changed = db.execute(
                "UPDATE capability_requests SET status=?,record_json=?,updated_at=? WHERE request_id=? AND status=?",
                (record["status"], json.dumps(record, sort_keys=True, separators=(",", ":")), record["updated_at"], record["request_id"], expected_status),
            )
            if changed.rowcount != 1:
                raise CapabilityLedgerError("capability state changed; approval is single-use")
        return record

    def resolve(
        self, request_id: str, *, profile: str, decision: str,
        approver: str, operator_nonce: str,
    ) -> dict[str, Any]:
        record = self.get(request_id)
        if record["profile"] != profile:
            raise CapabilityLedgerError("capability request belongs to another profile")
        if record["operator_nonce"] != operator_nonce:
            raise CapabilityLedgerError("operator nonce mismatch")
        self._identity("approver", approver)
        if record["status"] != "pending" or decision not in {"approved", "denied"}:
            raise CapabilityLedgerError("capability request cannot be resolved")
        record["status"] = decision
        record["approval_provenance"] = {"kind": "operator", "approver": approver, "decided_at": _now().isoformat()}
        return self._replace(record, expected_status="pending")

    def authorize(self, request_id: str, *, profile: str, job_id: str, argument_hash: str) -> dict[str, Any]:
        record = self.get(request_id)
        if record["status"] == "executing":
            raise CapabilityLedgerError("approval is single-use")
        if record["profile"] != profile or record["job_id"] != job_id or record["argument_hash"] != argument_hash:
            raise CapabilityLedgerError("approval binding mismatch")
        if record["status"] != "approved":
            raise CapabilityLedgerError("capability request is not approved")
        if datetime.fromisoformat(record["expires_at"]) <= _now():
            raise CapabilityLedgerError("capability approval expired")
        record["status"] = "executing"
        return self._replace(record, expected_status="approved")

    def complete(self, request_id: str, *, outcome: str, result_summary: dict[str, Any]) -> dict[str, Any]:
        record = self.get(request_id)
        if record["status"] != "executing" or outcome not in {"ok", "error"}:
            raise CapabilityLedgerError("capability request is not executing")
        validate_safe(result_summary, max_bytes=16_384)
        record["status"] = "executed" if outcome == "ok" else "failed"
        record["result_summary"] = result_summary
        return self._replace(record, expected_status="executing")
