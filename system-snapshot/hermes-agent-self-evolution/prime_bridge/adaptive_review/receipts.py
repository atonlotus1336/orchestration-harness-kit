"""Atomic receipt storage for adaptive review.

Wave 0 task 2: atomic receipt storage. Receipts are written atomically under
``target_project/.planning/review-evidence/<scope>/<content-id>/<run-id>/receipt.json``
and are reusable only when content identity plus policy and reviewer
fingerprints all match. Stale or malformed receipts fail closed, and an empty
scope can never PASS.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

RECEIPT_FILENAME = "receipt.json"
VALID_SCOPES = frozenset({"checkpoint", "plan", "phase", "system"})
VALID_VERDICTS = frozenset({"PASS", "FINDINGS", "INCONCLUSIVE"})
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")


class ReceiptError(ValueError):
    """Raised when a receipt is malformed, stale, or cannot be stored."""


@dataclass(frozen=True)
class ParallelReviewProvenance:
    """Closed provenance for a frozen parallel review stage."""

    scheduler_source_sha256: str
    catalog_sha256: str
    selected_members: tuple[str, ...]
    lens_bindings: Mapping[str, tuple[str, ...]]
    career_assignments: Mapping[str, str | None]
    grant_sha256s: Mapping[str, str]
    prompts_sha256: Mapping[str, str]
    budget: Mapping[str, Any]
    desired_count: int
    admitted_count: int
    observed_count: int
    frozen_triggers: tuple[str, ...] = ()
    career_shadow_results: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    convergence_owner: str = "Controller/Runner"

    def __post_init__(self) -> None:
        digests = (
            self.scheduler_source_sha256, self.catalog_sha256,
            *self.grant_sha256s.values(), *self.prompts_sha256.values(),
        )
        if any(not isinstance(value, str) or not DIGEST.fullmatch(value) for value in digests):
            raise ReceiptError("parallel review provenance contains an invalid digest")
        if (
            self.convergence_owner != "Controller/Runner"
            or len(self.selected_members) != len(set(self.selected_members))
            or not 0 <= self.observed_count <= self.admitted_count <= self.desired_count
            or self.desired_count > len(self.selected_members)
        ):
            raise ReceiptError("parallel review provenance bounds are invalid")
        selected = set(self.selected_members)
        if (
            set(self.lens_bindings) - selected
            or set(self.career_assignments) - selected
            or set(self.grant_sha256s) - selected
            or set(self.prompts_sha256) - selected
            or set(self.career_shadow_results) - selected
        ):
            raise ReceiptError("parallel review provenance references an unselected member")
        if len(self.frozen_triggers) != len(set(self.frozen_triggers)) or any(
            not isinstance(value, str) or not value for value in self.frozen_triggers
        ):
            raise ReceiptError("parallel review provenance contains invalid triggers")
        for result in self.career_shadow_results.values():
            if set(result) != {"status", "disposition", "finding_ids", "check_ids"}:
                raise ReceiptError("career Shadow result is not a closed contract")
            if result["status"] not in {"not_run", "completed", "inconclusive"}:
                raise ReceiptError("career Shadow status is invalid")
            if result["disposition"] not in {"disabled", "accepted", "rejected", "unresolved"}:
                raise ReceiptError("career Shadow disposition is invalid")
            if result["status"] == "not_run" and result["disposition"] != "disabled":
                raise ReceiptError("disabled career Shadow result is inconsistent")
            if result["status"] != "not_run" and result["disposition"] == "disabled":
                raise ReceiptError("enabled career Shadow result is inconsistent")
            for name in ("finding_ids", "check_ids"):
                values = result[name]
                if (
                    not isinstance(values, (tuple, list))
                    or len(values) > 3
                    or len(values) != len(set(values))
                    or any(not isinstance(value, str) or not value for value in values)
                ):
                    raise ReceiptError(f"career Shadow {name} is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheduler_source_sha256": self.scheduler_source_sha256,
            "catalog_sha256": self.catalog_sha256,
            "selected_members": list(self.selected_members),
            "lens_bindings": {
                key: list(self.lens_bindings[key]) for key in sorted(self.lens_bindings)
            },
            "career_assignments": dict(sorted(self.career_assignments.items())),
            "grant_sha256s": dict(sorted(self.grant_sha256s.items())),
            "prompts_sha256": dict(sorted(self.prompts_sha256.items())),
            "budget": dict(self.budget),
            "desired_count": self.desired_count,
            "admitted_count": self.admitted_count,
            "observed_count": self.observed_count,
            "frozen_triggers": list(self.frozen_triggers),
            "career_shadow_results": {
                key: {
                    "status": value["status"],
                    "disposition": value["disposition"],
                    "finding_ids": list(value["finding_ids"]),
                    "check_ids": list(value["check_ids"]),
                }
                for key, value in sorted(self.career_shadow_results.items())
            },
            "convergence_owner": self.convergence_owner,
        }


@dataclass(frozen=True)
class Receipt:
    """A stored review outcome for a specific scope/content/run."""

    scope: str
    content_id: str
    run_id: str
    policy_fingerprint: str
    reviewer_fingerprints: Mapping[str, str]
    verdict: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "scope": self.scope,
            "content_id": self.content_id,
            "run_id": self.run_id,
            "policy_fingerprint": self.policy_fingerprint,
            "reviewer_fingerprints": dict(sorted(self.reviewer_fingerprints.items())),
            "verdict": self.verdict,
        }
        if self.evidence:
            value["evidence"] = dict(self.evidence)
        return value


def _receipt_dir(
    target_project: Path, scope: str, content_id: str, run_id: str
) -> Path:
    if scope not in VALID_SCOPES:
        raise ReceiptError(f"unknown scope: {scope!r}")
    for label, value in (("content_id", content_id), ("run_id", run_id)):
        if not SAFE_SEGMENT.fullmatch(value) or value in {".", ".."}:
            raise ReceiptError(f"unsafe {label}: {value!r}")
    return (
        target_project
        / ".planning"
        / "review-evidence"
        / scope
        / content_id
        / run_id
    )


def _validate_verdict(verdict: str) -> None:
    if verdict not in VALID_VERDICTS:
        raise ReceiptError(f"unknown verdict: {verdict!r}")


def write_receipt_atomic(target_project: Path, receipt: Receipt) -> Path:
    """Atomically persist a receipt under the canonical evidence path."""
    if not receipt.scope:
        raise ReceiptError("empty scope can never PASS")
    _validate_verdict(receipt.verdict)
    dest_dir = _receipt_dir(
        target_project, receipt.scope, receipt.content_id, receipt.run_id
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / RECEIPT_FILENAME
    payload = json.dumps(receipt.to_dict(), sort_keys=True, indent=2)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".receipt-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


def _parse_receipt(data: Mapping[str, Any]) -> Receipt:
    try:
        receipt = Receipt(
            scope=str(data["scope"]),
            content_id=str(data["content_id"]),
            run_id=str(data["run_id"]),
            policy_fingerprint=str(data["policy_fingerprint"]),
            reviewer_fingerprints=dict(data["reviewer_fingerprints"]),
            verdict=str(data["verdict"]),
            evidence=dict(data.get("evidence", {})),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReceiptError(f"malformed receipt: {exc}") from exc
    _validate_verdict(receipt.verdict)
    if not receipt.scope:
        raise ReceiptError("empty scope can never PASS")
    return receipt


def load_receipt(
    target_project: Path, scope: str, content_id: str, run_id: str
) -> Receipt:
    """Load and validate a stored receipt; fail closed on malformed input."""
    dest = _receipt_dir(target_project, scope, content_id, run_id) / RECEIPT_FILENAME
    if not dest.exists():
        raise ReceiptError(f"receipt not found: {dest}")
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReceiptError(f"malformed receipt: {exc}") from exc
    return _parse_receipt(data)


def is_reusable(
    receipt: Receipt,
    content_id: str,
    policy_fingerprint: str,
    reviewer_fingerprint: str,
) -> bool:
    """A receipt is reusable only when identity and fingerprints all match.

    Stale receipts (mismatched content identity or fingerprints) and any
    non-PASS verdict fail closed.
    """
    if receipt.verdict != "PASS":
        return False
    if not receipt.scope:
        return False
    return (
        receipt.content_id == content_id
        and receipt.policy_fingerprint == policy_fingerprint
        and receipt.reviewer_fingerprints == {"default": reviewer_fingerprint}
    )
