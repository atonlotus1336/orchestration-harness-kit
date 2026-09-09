"""Projection of an existing operator decision; never an approval issuer.

The host supplies the existing capability ledger. No database is created or
migrated here. Broker execution must finish before a projection can be consumed.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

ALIAS = "review.budget.exception"
SERVER = "review-budget"
TOOL = "approve_exception"


class ExistingApprovalReader:
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        if not self.path.is_file():
            raise ValueError("existing capability ledger required")

    def get(self, request_id):
        try:
            with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute("SELECT record_json FROM capability_requests WHERE request_id=?",
                                 (request_id,)).fetchone()
        except sqlite3.Error as exc:
            raise ValueError("existing capability ledger is unreadable") from exc
        if not row or len(row[0]) > 100000:
            raise ValueError("approval record missing or oversized")
        return json.loads(row[0])


def _proposal(owner, identifier):
    from .controller import digest
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("invalid proposal identity")
    value = owner._read(owner.state / "budget-exceptions" / (identifier + ".json"))
    if digest(value) != identifier or value.get("status") != "draft" or value["expires_at"] <= time.time():
        raise ValueError("proposal altered or expired")
    return value


def _record(owner, proposal, identifier, request_id, status):
    ledger = owner.approval_ledger
    if ledger is None:
        raise ValueError("existing approval owner is not connected")
    value = ledger.get(request_id)
    args_hash = hashlib.sha256(json.dumps({"proposal_id": identifier},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    scope = proposal["binding"].get("approval_scope", {})
    provenance = value.get("approval_provenance") or {}
    expiry = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
    if (value.get("request_id") != request_id or value.get("status") != status
            or value.get("alias") != ALIAS or value.get("classification") != "action"
            or value.get("target") != {"server": SERVER, "tool": TOOL}
            or value.get("argument_hash") != args_hash
            or value.get("job_id") != scope.get("job_id") or not scope.get("job_id")
            or not value.get("profile")
            or provenance.get("kind") != "operator" or not provenance.get("approver")
            or not provenance.get("decided_at") or expiry.tzinfo is None
            or expiry.timestamp() <= time.time()):
        raise ValueError("exact operator approval is required")
    return value, min(proposal["expires_at"], int(expiry.timestamp()))


class ApprovalBridge:
    """Internal CapabilityBroker handler, behind its existing policy and lease gates."""
    def __init__(self, owner):
        self.owner = owner

    async def call(self, tool, arguments, context):
        from .controller import atomic, digest
        if tool != TOOL or set(arguments) != {"proposal_id"}:
            raise ValueError("closed approval projection request required")
        identifier = arguments["proposal_id"]
        proposal = _proposal(self.owner, identifier)
        record, expiry = _record(self.owner, proposal, identifier, context["request_id"], "executing")
        if any(context.get(k) != record[k] for k in ("job_id", "profile", "request_id")):
            raise ValueError("broker approval scope mismatch")
        # Revalidated later against the completed backing record, not this label.
        approval = {"schema_version": "review-budget-approval.v2", "proposal_id": identifier,
            "decision": "approve", "actor_kind": "human", "expires_at": expiry,
            "request_id": record["request_id"], "operator": digest(record["approval_provenance"])}
        atomic(self.owner.state / "approvals" / (identifier + ".json"), self.owner._signed(approval))
        return {"status": "projected", "proposal_id": identifier}


def validate(owner, identifier, proposal):
    from .controller import digest
    approval = owner._read(owner.state / "approvals" / (identifier + ".json"))
    if (set(approval) != {"schema_version", "proposal_id", "decision", "actor_kind", "expires_at", "request_id", "operator"}
            or approval["schema_version"] != "review-budget-approval.v2"
            or approval["proposal_id"] != identifier or approval["decision"] != "approve"
            or approval["actor_kind"] != "human" or type(approval["expires_at"]) is not int):
        raise ValueError("backed human approval projection required")
    record, expiry = _record(owner, proposal, identifier, approval["request_id"], "executed")
    if (approval["operator"] != digest(record["approval_provenance"])
            or not time.time() < approval["expires_at"] <= expiry):
        raise ValueError("operator approval changed or expired")
