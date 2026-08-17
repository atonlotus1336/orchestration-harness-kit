"""Namespaced Prime-local learning and host-owned promotion proposals."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prime_bridge.job_ledger import validate_safe


class LearningLedgerError(RuntimeError):
    pass


NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SUBJECT = re.compile(r"^[a-z][a-z0-9._-]{2,191}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SCOPES = {"session", "project", "profile"}
TARGET_SCOPES = {"project", "profile", "global"}
LANES = {"fact", "procedure", "configuration", "policy"}
PRIVACY = {"public", "internal", "restricted"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _name(label: str, value: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise LearningLedgerError(f"invalid {label}")
    return value


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode()).hexdigest()


class LearningLedger:
    def __init__(self, path: Path, *, max_content_bytes: int = 16_384):
        if max_content_bytes < 256:
            raise LearningLedgerError("learning content bound is too small")
        self.path = path
        self.max_content_bytes = max_content_bytes
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS learning_records (
                    record_id TEXT PRIMARY KEY, profile TEXT NOT NULL,
                    project TEXT NOT NULL, session_id TEXT NOT NULL,
                    scope TEXT NOT NULL, subject TEXT NOT NULL,
                    content_hash TEXT NOT NULL, status TEXT NOT NULL,
                    record_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS learning_scope
                    ON learning_records(profile,project,session_id,scope,status,updated_at);
                CREATE TABLE IF NOT EXISTS promotion_proposals (
                    proposal_id TEXT PRIMARY KEY, record_id TEXT NOT NULL,
                    profile TEXT NOT NULL, project TEXT NOT NULL,
                    lane TEXT NOT NULL, target_scope TEXT NOT NULL,
                    subject TEXT NOT NULL, dedupe_key TEXT NOT NULL,
                    status TEXT NOT NULL, record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(record_id) REFERENCES learning_records(record_id)
                );
                CREATE INDEX IF NOT EXISTS promotion_scope
                    ON promotion_proposals(profile,project,status,lane,updated_at);
                CREATE TABLE IF NOT EXISTS learning_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL, entity_id TEXT NOT NULL,
                    kind TEXT NOT NULL, data_json TEXT NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _event(db: sqlite3.Connection, entity_id: str, kind: str, data: dict[str, Any]) -> None:
        validate_safe(data, max_bytes=16_384)
        db.execute(
            "INSERT INTO learning_events(created_at,entity_id,kind,data_json) VALUES(?,?,?,?)",
            (_now().isoformat(), entity_id, kind, json.dumps(data, sort_keys=True, separators=(",", ":"))),
        )

    def record(
        self, *, profile: str, project: str, session_id: str, scope: str,
        subject: str, content: str, privacy: str, provenance: dict[str, Any],
        evidence: list[dict[str, Any]], expires_at: str | None = None,
    ) -> dict[str, Any]:
        for label, value in (("profile", profile), ("project", project), ("session", session_id)):
            _name(label, value)
        if scope not in SCOPES or privacy not in PRIVACY or not SUBJECT.fullmatch(subject):
            raise LearningLedgerError("invalid learning classification")
        if not isinstance(content, str) or not content.strip() or len(content.encode()) > self.max_content_bytes:
            raise LearningLedgerError("learning content is empty or oversized")
        if not isinstance(provenance, dict) or not isinstance(evidence, list) or not evidence:
            raise LearningLedgerError("provenance and evidence are required")
        validate_safe({"content": content, "provenance": provenance, "evidence": evidence})
        now = _now().isoformat()
        record = {
            "schema_version": "1.0", "record_id": uuid.uuid4().hex,
            "profile": profile, "project": project, "session_id": session_id,
            "scope": scope, "subject": subject, "content": content.strip(),
            "content_hash": _content_hash(content), "privacy": privacy,
            "provenance": provenance, "evidence": evidence,
            "status": "active", "created_at": now, "updated_at": now,
            "expires_at": expires_at,
        }
        validate_safe(record)
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO learning_records VALUES(?,?,?,?,?,?,?,?,?,?)",
                (record["record_id"], profile, project, session_id, scope, subject,
                 record["content_hash"], "active", encoded, now),
            )
            self._event(db, record["record_id"], "learning_recorded", {
                "scope": scope, "subject": subject, "content_hash": record["content_hash"],
            })
            db.commit()
        return record

    def get_record(self, record_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT record_json FROM learning_records WHERE record_id=?", (record_id,)).fetchone()
        if row is None:
            raise LearningLedgerError("unknown learning record")
        return json.loads(row[0])

    def list_records(
        self, *, profile: str, project: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _name("profile", profile)
        clauses, values = ["profile=?"], [profile]
        if project is not None:
            _name("project", project)
            clauses.append("project=?")
            values.append(project)
        if session_id is not None:
            _name("session", session_id)
            clauses.append("session_id=?")
            values.append(session_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT record_json FROM learning_records WHERE " + " AND ".join(clauses) + " ORDER BY updated_at,record_id",
                values,
            )
            return [json.loads(row[0]) for row in rows]

    def propose(
        self, record_id: str, *, profile: str, lane: str,
        target_scope: str, authority_rank: int = 10,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        record = self.get_record(record_id)
        if record["profile"] != profile:
            raise LearningLedgerError("learning record belongs to another profile")
        if lane not in LANES or target_scope not in TARGET_SCOPES:
            raise LearningLedgerError("invalid promotion lane or scope")
        if type(authority_rank) is not int or not 0 <= authority_rank <= 100:
            raise LearningLedgerError("invalid authority rank")
        if expires_at is None:
            expires_at = (_now() + timedelta(days=90)).isoformat()
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= _now():
            raise LearningLedgerError("promotion expiry must be in the future")
        proposal_id = uuid.uuid4().hex
        rollback_id = uuid.uuid4().hex
        dedupe_source = f"{target_scope}\0{record['subject']}\0{record['content_hash']}"
        dedupe_key = hashlib.sha256(dedupe_source.encode()).hexdigest()
        widening = {"session": 0, "project": 1, "profile": 2}[record["scope"]] < {
            "project": 1, "profile": 2, "global": 3,
        }[target_scope]
        now = _now().isoformat()
        proposal = {
            "schema_version": "1.0", "proposal_id": proposal_id,
            "record_id": record_id, "profile": profile, "project": record["project"],
            "lane": lane, "target_scope": target_scope, "subject": record["subject"],
            "privacy": record["privacy"], "provenance": record["provenance"],
            "evidence": record["evidence"], "content_hash": record["content_hash"],
            "dedupe_key": dedupe_key, "authority_rank": authority_rank,
            "scope_widening": widening, "validation": None,
            "status": "proposed", "decision": None, "artifact": None,
            "rollback_id": rollback_id, "created_at": now, "updated_at": now,
            "expires_at": expiry.isoformat(),
        }
        validate_safe(proposal)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO promotion_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (proposal_id, record_id, profile, record["project"], lane, target_scope,
                 record["subject"], dedupe_key, "proposed",
                 json.dumps(proposal, sort_keys=True, separators=(",", ":")), now),
            )
            self._event(db, proposal_id, "promotion_proposed", {
                "lane": lane, "target_scope": target_scope, "dedupe_key": dedupe_key,
            })
            db.commit()
        return proposal

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT record_json FROM promotion_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if row is None:
            raise LearningLedgerError("unknown promotion proposal")
        return json.loads(row[0])

    def list_proposals(self, *, profile: str, status: str | None = None) -> list[dict[str, Any]]:
        _name("profile", profile)
        query, values = "SELECT record_json FROM promotion_proposals WHERE profile=?", [profile]
        if status is not None:
            query += " AND status=?"
            values.append(status)
        query += " ORDER BY updated_at,proposal_id"
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute(query, values)]

    def update(
        self, proposal_id: str, *, expected: set[str], status: str,
        changes: dict[str, Any], event: str,
    ) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM promotion_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row is None:
                raise LearningLedgerError("unknown promotion proposal")
            proposal = json.loads(row[0])
            if proposal["status"] not in expected:
                raise LearningLedgerError("invalid promotion state transition")
            proposal.update(changes)
            proposal["status"] = status
            proposal["updated_at"] = _now().isoformat()
            validate_safe(proposal)
            db.execute(
                "UPDATE promotion_proposals SET status=?,record_json=?,updated_at=? WHERE proposal_id=?",
                (status, json.dumps(proposal, sort_keys=True, separators=(",", ":")), proposal["updated_at"], proposal_id),
            )
            self._event(db, proposal_id, event, {"status": status, **{
                key: value for key, value in changes.items() if key in {"decision", "validation", "artifact"}
            }})
            db.commit()
        return proposal

    def validate(
        self, proposal_id: str, *, validator: str, mode: str,
        outcome: str, evidence_hash: str,
    ) -> dict[str, Any]:
        _name("validator", validator)
        if mode not in {"deterministic", "independent", "manual"} or outcome not in {"passed", "failed"}:
            raise LearningLedgerError("invalid validation result")
        if not HEX64.fullmatch(evidence_hash):
            raise LearningLedgerError("validation evidence hash is required")
        validation = {
            "validator": validator, "mode": mode, "outcome": outcome,
            "evidence_hash": evidence_hash, "validated_at": _now().isoformat(),
        }
        return self.update(
            proposal_id, expected={"proposed", "validation_failed"},
            status="validated" if outcome == "passed" else "validation_failed",
            changes={"validation": validation}, event="promotion_validated",
        )

    def events(self, entity_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT created_at,kind,data_json FROM learning_events WHERE entity_id=? ORDER BY event_id",
                (entity_id,),
            )
            return [{"created_at": row[0], "kind": row[1], "data": json.loads(row[2])} for row in rows]
