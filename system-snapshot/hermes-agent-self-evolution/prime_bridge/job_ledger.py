"""Small durable Hermes control ledger for Prime jobs."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class LedgerError(RuntimeError):
    pass


STATUSES = {"created", "starting", "running", "stopping", "completed", "failed", "cancelled", "orphaned"}
TERMINAL = {"completed", "failed", "cancelled", "orphaned"}
TRANSITIONS = {
    "created": {"starting", "cancelled"},
    "starting": {"running", "failed", "cancelled", "orphaned"},
    "running": {"stopping", "completed", "failed", "cancelled", "orphaned"},
    "stopping": {"cancelled", "failed", "orphaned"},
}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CREDENTIAL_SHAPE = re.compile(r"(?:-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}\b|\bAKIA[0-9A-Z]{16}\b)")
FORBIDDEN_KEY = re.compile(r"(?:api.?key|password|secret|token)", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise LedgerError("timezone-aware timestamp required")
    return result


def validate_safe(value: Any, *, max_bytes: int = 65_536) -> None:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(text.encode()) > max_bytes:
        raise LedgerError("job metadata exceeds the bounded storage limit")
    def keys(item: Any) -> bool:
        if isinstance(item, dict):
            return any(FORBIDDEN_KEY.search(str(key)) or keys(child) for key, child in item.items())
        if isinstance(item, list):
            return any(keys(child) for child in item)
        return False
    if CREDENTIAL_SHAPE.search(text) or keys(value):
        raise LedgerError("secret-shaped job metadata is forbidden")


class JobLedger:
    def __init__(self, path: Path, allowed_roots: Iterable[Path]):
        self.path = path
        self.allowed_roots = tuple(root.resolve(strict=True) for root in allowed_roots)
        if not self.allowed_roots:
            raise LedgerError("at least one workspace root is required")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, record_json TEXT NOT NULL,
                    status TEXT NOT NULL, profile TEXT NOT NULL, project TEXT NOT NULL,
                    updated_at TEXT NOT NULL, lease_owner TEXT, lease_expires_at TEXT,
                    version INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_scope ON jobs(profile, project, status);
                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
                CREATE TABLE IF NOT EXISTS job_commands (
                    command_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, kind TEXT NOT NULL, text TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
                CREATE INDEX IF NOT EXISTS commands_pending ON job_commands(job_id,status,created_at);
                CREATE TABLE IF NOT EXISTS child_jobs (
                    child_job_id TEXT PRIMARY KEY, parent_job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(parent_job_id) REFERENCES jobs(job_id)
                );
                CREATE INDEX IF NOT EXISTS child_jobs_parent ON child_jobs(parent_job_id,status);
            """)

    def _workspace(self, value: str | Path) -> str:
        path = Path(value).resolve(strict=True)
        if not path.is_dir() or not any(path == root or path.is_relative_to(root) for root in self.allowed_roots):
            raise LedgerError("workspace is outside an allowed project root")
        return str(path)

    @staticmethod
    def _name(label: str, value: str) -> str:
        if not NAME.fullmatch(value):
            raise LedgerError(f"invalid {label}")
        return value

    def create(
        self, *, owner: str, profile: str, project: str, session_id: str,
        workspace: str | Path, route: dict[str, str], limits: dict[str, int],
        runtime: dict[str, str],
        job_id: str | None = None,
    ) -> dict[str, Any]:
        for label, value in (("owner", owner), ("profile", profile), ("project", project), ("session_id", session_id)):
            self._name(label, value)
        required_route = {"provider", "model", "api", "endpoint", "fingerprint"}
        if set(route) != required_route or not all(isinstance(route.get(key), str) for key in required_route):
            raise LedgerError("closed route record required")
        validate_safe(route, max_bytes=16_384)
        if not re.fullmatch(r"[0-9a-f]{64}", route["fingerprint"]):
            raise LedgerError("closed route record required")
        if route["fingerprint"] != self.route_fingerprint(route):
            raise LedgerError("route fingerprint mismatch")
        try:
            endpoint = urlsplit(route["endpoint"])
            port = endpoint.port
        except ValueError as exc:
            raise LedgerError("invalid model endpoint") from exc
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or port in {8642, 9119}
        ):
            raise LedgerError("invalid model endpoint")
        if set(limits) != {"max_iterations", "no_progress_seconds", "wall_seconds"} or any(type(v) is not int or v < 1 for v in limits.values()):
            raise LedgerError("positive closed loop limits required")
        required_runtime = {"source_commit", "image_digest", "sandbox", "command_fingerprint", "fingerprint"}
        if set(runtime) != required_runtime or not all(isinstance(runtime.get(key), str) for key in required_runtime) or runtime["sandbox"] != "rootless-podman":
            raise LedgerError("closed rootless runtime record required")
        if not re.fullmatch(r"[0-9a-f]{40}", runtime["source_commit"]):
            raise LedgerError("invalid Prime source commit")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["image_digest"]):
            raise LedgerError("invalid Prime image digest")
        if not re.fullmatch(r"[0-9a-f]{64}", runtime["command_fingerprint"]):
            raise LedgerError("invalid runtime command fingerprint")
        if runtime["fingerprint"] != self.runtime_fingerprint(runtime):
            raise LedgerError("runtime fingerprint mismatch")
        now = utc_now()
        value: dict[str, Any] = {
            "schema_version": "1.0", "job_id": job_id or uuid.uuid4().hex,
            "owner": owner, "profile": profile, "project": project, "session_id": session_id,
            "workspace": self._workspace(workspace), "status": "created",
            "created_at": now, "updated_at": now, "started_at": None, "finished_at": None,
            "route": route, "runtime": runtime, "limits": limits, "iteration": 0, "last_progress_at": now,
            "prime_session_id": None, "trace_ref": None, "result_summary": None,
            "last_error": None, "recovery": {}, "lease": {"owner": None, "expires_at": None},
            "version": 0,
        }
        if not re.fullmatch(r"[0-9a-f]{32}", value["job_id"]):
            raise LedgerError("job_id must be 32 lowercase hex characters")
        validate_safe(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO jobs(job_id,record_json,status,profile,project,updated_at,version) VALUES(?,?,?,?,?,?,0)",
                    (value["job_id"], encoded, "created", profile, project, now),
                )
                self._event(db, value["job_id"], "created", {"route_fingerprint": route["fingerprint"]})
                db.commit()
        except sqlite3.IntegrityError as exc:
            raise LedgerError("job already exists") from exc
        return value

    @staticmethod
    def _event(db: sqlite3.Connection, job_id: str, kind: str, data: dict[str, Any]) -> None:
        validate_safe(data, max_bytes=16_384)
        db.execute(
            "INSERT INTO job_events(job_id,created_at,kind,data_json) VALUES(?,?,?,?)",
            (job_id, utc_now(), kind, json.dumps(data, sort_keys=True, separators=(",", ":"))),
        )

    def get(self, job_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT record_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise LedgerError("unknown job")
        return json.loads(row[0])

    def list(self, *, profile: str | None = None, nonterminal: bool = False) -> list[dict[str, Any]]:
        clauses, values = [], []
        if profile is not None:
            self._name("profile", profile); clauses.append("profile=?"); values.append(profile)
        if nonterminal:
            clauses.append("status NOT IN ('completed','failed','cancelled','orphaned')")
        query = "SELECT record_json FROM jobs" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY updated_at,job_id"
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute(query, values)]

    def events(
        self, job_id: str, *, after_event_id: int = 0, limit: int = 200,
    ) -> list[dict[str, Any]]:
        self.get(job_id)
        if type(after_event_id) is not int or after_event_id < 0:
            raise LedgerError("after_event_id must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise LedgerError("event limit must be between 1 and 200")
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_id,created_at,kind,data_json FROM job_events "
                "WHERE job_id=? AND event_id>? ORDER BY event_id LIMIT ?",
                (job_id, after_event_id, limit),
            )
            return [
                {"event_id": row[0], "created_at": row[1], "kind": row[2], "data": json.loads(row[3])}
                for row in rows
            ]

    def enqueue_command(self, job_id: str, kind: str, text: str | None = None) -> str:
        if kind not in {"steer", "cancel"}:
            raise LedgerError("invalid operator command")
        if kind == "steer" and (not isinstance(text, str) or not text.strip()):
            raise LedgerError("steer text is required")
        if kind == "cancel" and text is not None:
            raise LedgerError("cancel text is forbidden")
        self.get(job_id)
        validate_safe({"text": text}, max_bytes=16_384)
        command_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise LedgerError("unknown job")
            if row[0] in TERMINAL:
                raise LedgerError("terminal job cannot accept commands")
            if kind == "steer" and row[0] != "running":
                raise LedgerError("steer requires a running job")
            db.execute(
                "INSERT INTO job_commands(command_id,job_id,created_at,kind,text) VALUES(?,?,?,?,?)",
                (command_id, job_id, utc_now(), kind, text),
            )
            self._event(db, job_id, "command_queued", {"command_id": command_id, "kind": kind})
            db.commit()
        return command_id

    def pending_commands(self, job_id: str) -> list[dict[str, Any]]:
        self.get(job_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT command_id,kind,text,created_at FROM job_commands WHERE job_id=? AND status='pending' ORDER BY created_at,command_id",
                (job_id,),
            )
            return [dict(row) for row in rows]

    def acknowledge_command(self, job_id: str, command_id: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE job_commands SET status='applied' WHERE job_id=? AND command_id=? AND status='dispatching'",
                (job_id, command_id),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise LedgerError("unknown or already-applied command")
            self._event(db, job_id, "command_applied", {"command_id": command_id})
            db.commit()

    def claim_command(self, job_id: str, command_id: str) -> None:
        """Make delivery non-replayable before crossing the RPC boundary."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE job_commands SET status='dispatching' WHERE job_id=? AND command_id=? AND status='pending'",
                (job_id, command_id),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise LedgerError("unknown or already-claimed command")
            self._event(db, job_id, "command_dispatching", {"command_id": command_id})
            db.commit()

    def _mutate(self, job_id: str, update: Any, event: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json,version FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise LedgerError("unknown job")
            value = json.loads(row[0]); update(value); value["updated_at"] = utc_now(); value["version"] = row[1] + 1
            validate_safe(value)
            db.execute(
                "UPDATE jobs SET record_json=?,status=?,updated_at=?,lease_owner=?,lease_expires_at=?,version=? WHERE job_id=? AND version=?",
                (json.dumps(value, sort_keys=True, separators=(",", ":")), value["status"], value["updated_at"], value["lease"]["owner"], value["lease"]["expires_at"], value["version"], job_id, row[1]),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise LedgerError("stale job write")
            self._event(db, job_id, event, data); db.commit(); return value

    def transition(self, job_id: str, status: str, **fields: Any) -> dict[str, Any]:
        if status not in STATUSES or set(fields) - {"prime_session_id", "trace_ref", "result_summary", "last_error", "recovery"}:
            raise LedgerError("invalid transition payload")
        def apply(value: dict[str, Any]) -> None:
            current = value["status"]
            if current in TERMINAL or status not in TRANSITIONS.get(current, set()):
                raise LedgerError(f"invalid transition {current}->{status}")
            value["status"] = status; value.update(fields)
            if status == "running" and value["started_at"] is None:
                value["started_at"] = utc_now()
                value["last_progress_at"] = value["started_at"]
            if status in TERMINAL: value["finished_at"] = utc_now(); value["lease"] = {"owner": None, "expires_at": None}
        return self._mutate(job_id, apply, "transition", {"status": status})

    def acquire(self, job_id: str, owner: str, ttl_seconds: int, *, now: datetime | None = None) -> dict[str, Any]:
        self._name("lease owner", owner)
        if type(ttl_seconds) is not int or ttl_seconds < 1: raise LedgerError("positive lease ttl required")
        instant = now or datetime.now(timezone.utc); expires = (instant + timedelta(seconds=ttl_seconds)).isoformat()
        def apply(value: dict[str, Any]) -> None:
            if value["status"] in TERMINAL:
                raise LedgerError("terminal job cannot acquire a lease")
            lease = value["lease"]
            if lease["owner"] not in (None, owner) and _parse_time(lease["expires_at"]) > instant:
                raise LedgerError("loop owner already active")
            value["lease"] = {"owner": owner, "expires_at": expires}
        return self._mutate(job_id, apply, "lease_acquired", {"owner": owner, "expires_at": expires})

    def renew(self, job_id: str, owner: str, ttl_seconds: int) -> dict[str, Any]:
        self._name("lease owner", owner)
        if type(ttl_seconds) is not int or ttl_seconds < 1:
            raise LedgerError("positive lease ttl required")
        instant = datetime.now(timezone.utc)
        def apply(value: dict[str, Any]) -> None:
            lease = value["lease"]
            if lease["owner"] != owner:
                raise LedgerError("not loop owner")
            if lease["expires_at"] is None or _parse_time(lease["expires_at"]) <= instant:
                raise LedgerError("loop lease expired")
            value["lease"]["expires_at"] = (instant + timedelta(seconds=ttl_seconds)).isoformat()
        return self._mutate(job_id, apply, "lease_renewed", {"owner": owner})

    def progress(self, job_id: str, owner: str, *, prime_session_id: str | None = None, trace_ref: str | None = None) -> dict[str, Any]:
        instant = datetime.now(timezone.utc)
        def apply(value: dict[str, Any]) -> None:
            lease = value["lease"]
            if lease["owner"] != owner or value["status"] != "running":
                raise LedgerError("active loop owner required")
            if lease["expires_at"] is None or _parse_time(lease["expires_at"]) <= instant:
                raise LedgerError("loop lease expired")
            value["iteration"] += 1; value["last_progress_at"] = utc_now()
            if prime_session_id is not None: value["prime_session_id"] = prime_session_id
            if trace_ref is not None: value["trace_ref"] = trace_ref
        return self._mutate(job_id, apply, "progress", {"prime_session_id": prime_session_id})

    def note(self, job_id: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        """Append a bounded audit event without changing lifecycle state."""
        self._name("event kind", kind)
        return self._mutate(job_id, lambda _value: None, kind, data)

    def recover(self, job_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Record deterministic restart/reconciliation state."""
        validate_safe(data)
        return self._mutate(
            job_id,
            lambda value: value.__setitem__("recovery", data),
            "recovery",
            data,
        )

    def lease_expired(self, job_id: str, *, now: datetime | None = None) -> bool:
        lease = self.get(job_id)["lease"]
        if lease["owner"] is None or lease["expires_at"] is None:
            return True
        return _parse_time(lease["expires_at"]) <= (now or datetime.now(timezone.utc))

    @staticmethod
    def route_fingerprint(route: dict[str, str]) -> str:
        public = {key: route[key] for key in ("provider", "model", "api", "endpoint")}
        return hashlib.sha256(json.dumps(public, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def runtime_fingerprint(runtime: dict[str, str]) -> str:
        public = {key: runtime[key] for key in ("source_commit", "image_digest", "sandbox", "command_fingerprint")}
        return hashlib.sha256(json.dumps(public, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def create_child(
        self, *, parent_job_id: str, kind: str, record: dict[str, Any],
        child_job_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a child OMP job bound to a parent Prime job (durable)."""
        if kind not in {"omp_worker", "omp_adapter:lsp", "omp_adapter:hash_edit"}:
            raise LedgerError("invalid child job kind")
        self.get(parent_job_id)
        validate_safe(record)
        child_id = child_job_id or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", child_id):
            raise LedgerError("child_job_id must be 32 lowercase hex characters")
        now = utc_now()
        value = {
            "schema_version": "1.0", "child_job_id": child_id,
            "parent_job_id": parent_job_id, "kind": kind, "status": "created",
            "created_at": now, "updated_at": now, "record": record,
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO child_jobs(child_job_id,parent_job_id,created_at,kind,status,record_json) VALUES(?,?,?,?,?,?)",
                    (child_id, parent_job_id, now, kind, "created", encoded),
                )
                self._event(db, parent_job_id, "child_created", {"child_job_id": child_id, "kind": kind})
                db.commit()
        except sqlite3.IntegrityError as exc:
            raise LedgerError("child job already exists") from exc
        return value

    def child(self, child_job_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT record_json FROM child_jobs WHERE child_job_id=?", (child_job_id,)).fetchone()
        if row is None:
            raise LedgerError("unknown child job")
        return json.loads(row[0])

    def children(self, parent_job_id: str) -> list[dict[str, Any]]:
        self.get(parent_job_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT record_json FROM child_jobs WHERE parent_job_id=? ORDER BY created_at,child_job_id",
                (parent_job_id,),
            )
            return [json.loads(row[0]) for row in rows]

    def update_child(self, child_job_id: str, *, status: str, record: dict[str, Any]) -> dict[str, Any]:
        if status not in {"created", "starting", "running", "completed", "failed", "cancelled"}:
            raise LedgerError("invalid child job status")
        validate_safe(record)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json,parent_job_id FROM child_jobs WHERE child_job_id=?", (child_job_id,)).fetchone()
            if row is None:
                raise LedgerError("unknown child job")
            value = json.loads(row[0])
            value["status"] = status
            value["updated_at"] = utc_now()
            value["record"] = record
            db.execute(
                "UPDATE child_jobs SET status=?,record_json=? WHERE child_job_id=?",
                (status, json.dumps(value, sort_keys=True, separators=(",", ":")), child_job_id),
            )
            self._event(db, row[1], "child_status", {"child_job_id": child_job_id, "status": status})
            db.commit()
        return value
