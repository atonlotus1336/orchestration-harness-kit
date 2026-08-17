"""Hermes-owned promotion engine for shared learning artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prime_bridge.learning_ledger import LearningLedger
from prime_bridge.promotion_policy import PromotionPolicy


class PromotionEngineError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


class PromotionEngine:
    def __init__(
        self, ledger: LearningLedger, policy: PromotionPolicy,
        *, shared_root: Path | None = None,
    ):
        self.ledger = ledger
        self.policy = policy
        self.shared_root = (shared_root or policy.shared_root).resolve()
        self.index_path = self.shared_root / ".promotion-index.json"

    def _index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema_version": "1.0", "entries": []}
        value = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"schema_version", "entries"} or value["schema_version"] != "1.0":
            raise PromotionEngineError("shared promotion index is invalid")
        return value

    def _save_index(self, value: dict[str, Any]) -> None:
        _atomic_write(self.index_path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())

    @staticmethod
    def _slug(subject: str) -> str:
        return re.sub(r"[^a-z0-9._-]+", "-", subject.lower()).strip("-.")[:96]

    def _artifact_path(self, proposal: dict[str, Any]) -> Path:
        if proposal["target_scope"] == "global":
            scope = Path("global")
        elif proposal["target_scope"] == "profile":
            scope = Path("profiles") / proposal["profile"]
        else:
            scope = Path("projects") / proposal["project"]
        filename = f"{self._slug(proposal['subject'])}-{proposal['dedupe_key'][:12]}.md"
        path = (self.shared_root / scope / filename).resolve()
        if not path.is_relative_to(self.shared_root):
            raise PromotionEngineError("promotion artifact escaped shared root")
        return path

    @staticmethod
    def _render(proposal: dict[str, Any], record: dict[str, Any]) -> bytes:
        validation = proposal["validation"] or {}
        frontmatter = {
            "prime_promotion": proposal["proposal_id"],
            "subject": proposal["subject"], "lane": proposal["lane"],
            "scope": proposal["target_scope"], "profile": proposal["profile"],
            "project": proposal["project"], "privacy": proposal["privacy"],
            "content_sha256": proposal["content_hash"],
            "validation_mode": validation.get("mode"),
            "validation_evidence_sha256": validation.get("evidence_hash"),
            "rollback_id": proposal["rollback_id"], "expires_at": proposal["expires_at"],
        }
        lines = ["---", *[f"{key}: {json.dumps(value)}" for key, value in frontmatter.items()], "---", "", record["content"], ""]
        return "\n".join(lines).encode()

    def evaluate(self, proposal_id: str, *, operator_override: bool = False) -> dict[str, Any]:
        proposal = self.ledger.get_proposal(proposal_id)
        if datetime.fromisoformat(proposal["expires_at"]) <= _now():
            return self.ledger.update(
                proposal_id, expected={"proposed", "validated", "validation_failed", "approval_required"},
                status="expired", changes={"decision": {"action": "expired"}}, event="promotion_expired",
            )
        if proposal["status"] not in {"validated", "approved"}:
            raise PromotionEngineError("promotion is not ready for evaluation")
        decision = self.policy.decide(proposal)
        if operator_override and proposal["status"] == "approved":
            action, reason = "auto_promote", "operator approved"
        else:
            action, reason = decision.action, decision.reason
        if action in {"approval_required", "validation_required", "transaction_required", "rejected"}:
            status = "approval_required" if action in {"approval_required", "transaction_required"} else action
            return self.ledger.update(
                proposal_id, expected={proposal["status"]}, status=status,
                changes={"decision": {"action": action, "reason": reason}}, event="promotion_decided",
            )
        if action != "auto_promote":
            raise PromotionEngineError("unsupported promotion decision")
        index = self._index()
        active = [item for item in index["entries"] if item["status"] == "active"]
        duplicate = next((item for item in active if item["dedupe_key"] == proposal["dedupe_key"]), None)
        if duplicate is not None:
            return self.ledger.update(
                proposal_id, expected={proposal["status"]}, status="deduplicated",
                changes={"decision": {"action": "deduplicated", "existing": duplicate["proposal_id"]},
                         "artifact": duplicate["artifact"]}, event="promotion_deduplicated",
            )
        conflict = next((
            item for item in active
            if item["target_scope"] == proposal["target_scope"]
            and item["subject"] == proposal["subject"]
            and item["content_hash"] != proposal["content_hash"]
        ), None)
        if conflict is not None and conflict["authority_rank"] >= proposal["authority_rank"]:
            return self.ledger.update(
                proposal_id, expected={proposal["status"]}, status="conflict",
                changes={"decision": {"action": "conflict", "authority": conflict["proposal_id"]}},
                event="promotion_conflict",
            )
        if conflict is not None:
            conflict["status"] = "superseded"
            conflict["superseded_by"] = proposal_id
        record = self.ledger.get_record(proposal["record_id"])
        artifact = self._artifact_path(proposal)
        encoded = self._render(proposal, record)
        _atomic_write(artifact, encoded)
        relative = artifact.relative_to(self.shared_root).as_posix()
        entry = {
            "proposal_id": proposal_id, "dedupe_key": proposal["dedupe_key"],
            "subject": proposal["subject"], "content_hash": proposal["content_hash"],
            "target_scope": proposal["target_scope"], "authority_rank": proposal["authority_rank"],
            "artifact": relative, "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
            "rollback_id": proposal["rollback_id"], "status": "active",
        }
        index["entries"].append(entry)
        self._save_index(index)
        return self.ledger.update(
            proposal_id, expected={proposal["status"]}, status="promoted",
            changes={"decision": {"action": "promoted", "reason": reason}, "artifact": entry},
            event="promotion_applied",
        )

    def resolve(self, proposal_id: str, *, decision: str, approver: str) -> dict[str, Any]:
        if decision not in {"approved", "denied"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", approver):
            raise PromotionEngineError("invalid operator promotion decision")
        proposal = self.ledger.get_proposal(proposal_id)
        if proposal["status"] != "approval_required":
            raise PromotionEngineError("promotion is not awaiting approval")
        updated = self.ledger.update(
            proposal_id, expected={"approval_required"}, status=decision,
            changes={"decision": {"action": decision, "approver": approver, "decided_at": _now().isoformat()}},
            event="promotion_operator_decision",
        )
        return self.evaluate(proposal_id, operator_override=True) if decision == "approved" and updated["lane"] != "configuration" else updated

    def apply_configuration(
        self, proposal_id: str, *, transaction: Any, envelope_name: str,
        expected_before_sha256: str, new_bytes: bytes,
        local_verify: Any, remote_verify: Any | None = None,
    ) -> dict[str, Any]:
        proposal = self.ledger.get_proposal(proposal_id)
        if proposal["lane"] != "configuration" or proposal["status"] != "approved":
            raise PromotionEngineError("configuration promotion is not approved")
        receipt = transaction.apply(
            envelope_name=envelope_name, expected_before_sha256=expected_before_sha256,
            new_bytes=new_bytes, rollback_id=proposal["rollback_id"],
            local_verify=local_verify, remote_verify=remote_verify,
        )
        status = "configured" if receipt["status"] == "applied" else "rolled_back"
        return self.ledger.update(
            proposal_id, expected={"approved"}, status=status,
            changes={"decision": {"action": status}, "artifact": receipt},
            event="configuration_applied" if status == "configured" else "configuration_rolled_back",
        )

    def expire(self, *, profile: str) -> list[str]:
        expired = []
        for proposal in self.ledger.list_proposals(profile=profile):
            if proposal["status"] in {"proposed", "validated", "validation_failed", "approval_required"} and datetime.fromisoformat(proposal["expires_at"]) <= _now():
                self.ledger.update(
                    proposal["proposal_id"], expected={proposal["status"]}, status="expired",
                    changes={"decision": {"action": "expired"}}, event="promotion_expired",
                )
                expired.append(proposal["proposal_id"])
        return expired

    def revoke(self, proposal_id: str, *, approver: str, reason: str) -> dict[str, Any]:
        proposal = self.ledger.get_proposal(proposal_id)
        if proposal["status"] != "promoted" or not reason.strip():
            raise PromotionEngineError("only an active promotion can be revoked")
        index = self._index()
        entry = next((item for item in index["entries"] if item["proposal_id"] == proposal_id and item["status"] == "active"), None)
        if entry is None:
            raise PromotionEngineError("active promotion artifact is missing")
        artifact = (self.shared_root / entry["artifact"]).resolve()
        if not artifact.is_relative_to(self.shared_root) or not artifact.is_file():
            raise PromotionEngineError("active promotion artifact is missing")
        rollback = self.shared_root / ".rollback" / proposal["rollback_id"] / artifact.name
        _atomic_write(rollback, artifact.read_bytes())
        artifact.unlink()
        entry["status"] = "revoked"
        entry["revoked_by"] = approver
        entry["rollback_artifact"] = rollback.relative_to(self.shared_root).as_posix()
        self._save_index(index)
        return self.ledger.update(
            proposal_id, expected={"promoted"}, status="revoked",
            changes={"decision": {"action": "revoked", "approver": approver, "reason": reason},
                     "artifact": entry}, event="promotion_revoked",
        )
