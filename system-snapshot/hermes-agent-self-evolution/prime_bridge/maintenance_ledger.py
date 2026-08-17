"""Secret-safe append-only maintenance records and known-good restore points."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

COMPONENTS = ("hermes", "prime", "omp")
OUTCOMES = {"PROMOTED", "FAILED_RESTORED", "SKIPPED"}
HALT_CLASSES = {"ROLLBACK_UNVERIFIED", "INTEGRITY_FAILURE", "CREDENTIAL_FAILURE", "SHARED_INFRASTRUCTURE_FAILURE"}
SECRET_KEY = re.compile(r"(?:api.?key|password|secret|token|credential|authorization|cookie)", re.I)


class MaintenanceLedgerError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if SECRET_KEY.search(str(key)):
                raise MaintenanceLedgerError("secret-shaped metadata is forbidden")
            _safe(child)
    elif isinstance(value, list):
        for child in value:
            _safe(child)
    elif isinstance(value, str) and len(value) > 4096:
        raise MaintenanceLedgerError("metadata value is too large")


class MaintenanceLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS maintenance_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                component TEXT NOT NULL, outcome TEXT, halt_class TEXT,
                created_at TEXT NOT NULL, record_json TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS restore_points (
                restore_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                component TEXT NOT NULL, created_at TEXT NOT NULL, manifest_json TEXT NOT NULL
              );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def snapshot(self, run_id: str, component: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
        if component not in COMPONENTS:
            raise MaintenanceLedgerError("unknown component")
        _safe(manifest)
        encoded = json.dumps(dict(manifest), sort_keys=True, separators=(",", ":"))
        record = {"restore_id": uuid.uuid4().hex, "run_id": run_id, "component": component,
                  "created_at": _now(), "manifest": dict(manifest),
                  "manifest_sha256": hashlib.sha256(encoded.encode()).hexdigest()}
        with self._connect() as db:
            db.execute("INSERT INTO restore_points VALUES(?,?,?,?,?)", (record["restore_id"], run_id, component, record["created_at"], json.dumps(record, sort_keys=True)))
        return record

    def record(self, run_id: str, component: str, *, outcome: str | None = None,
               halt_class: str | None = None, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if component not in COMPONENTS:
            raise MaintenanceLedgerError("unknown component")
        if outcome is not None and outcome not in OUTCOMES:
            raise MaintenanceLedgerError("invalid component outcome")
        if halt_class is not None and halt_class not in HALT_CLASSES:
            raise MaintenanceLedgerError("invalid halt class")
        if (outcome is None) == (halt_class is None):
            raise MaintenanceLedgerError("record exactly one outcome or halt class")
        payload = dict(data or {})
        _safe(payload)
        record = {"schema_version": "1.0", "event_id": uuid.uuid4().hex, "run_id": run_id,
                  "component": component, "outcome": outcome, "halt_class": halt_class,
                  "created_at": _now(), "data": payload}
        with self._connect() as db:
            db.execute("INSERT INTO maintenance_events(run_id,component,outcome,halt_class,created_at,record_json) VALUES(?,?,?,?,?,?)",
                       (run_id, component, outcome, halt_class, record["created_at"], json.dumps(record, sort_keys=True)))
        return record

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT record_json FROM maintenance_events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]
