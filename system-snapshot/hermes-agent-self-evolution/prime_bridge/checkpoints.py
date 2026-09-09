"""Durable continuation checkpoints for intentional governance stops."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


_SAFE_EXECUTION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GOVERNED_LANES = {"T3_PRIME_OMP", "T4_AUTONOMOUS"}
_STEP_STATUSES = {"pending", "completed", "blocked"}
_PENDING_WRITE_STATUSES = {"not_started", "pending", "committed", "discarded", "unknown"}


def _atomic_json(path: Path, value: MappingLike) -> Path:
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > 65_536:
        raise ValueError("checkpoint exceeds the bounded storage limit")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    return path


def write_checkpoint(
    root: Path, *, execution_id: str, task_sha256: str, declared_paths: Sequence[str],
    completed_units: Sequence[MappingLike], pending_units: Sequence[MappingLike],
    blocker: str, evidence: Any, next_safe_action: str,
) -> Path:
    if not _SAFE_EXECUTION_ID.fullmatch(execution_id):
        raise ValueError("execution_id is not safe for a checkpoint filename")
    if not _SHA256.fullmatch(task_sha256):
        raise ValueError("task_sha256 must be a lowercase SHA-256 digest")
    checkpoint_root = root / "checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    path = checkpoint_root / f"{execution_id}.json"
    value = {
        "schema_version": "1.0",
        "execution_id": execution_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "task_sha256": task_sha256,
        "declared_paths": sorted(set(str(path).replace("\\", "/") for path in declared_paths)),
        "completed_units": list(completed_units),
        "pending_units": list(pending_units),
        "exact_blocker": blocker,
        "evidence": evidence,
        "next_safe_action": next_safe_action,
    }
    return _atomic_json(path, value)


def write_step_checkpoint(
    root: Path, *, execution_id: str, execution_lane: str, step_id: str,
    input_fingerprint: str, idempotency_key: str, status: str,
    pending_write_status: str, output_reference: str | None = None,
    evidence: Any = None, next_safe_action: str,
) -> Path:
    """Write resumable step state for governed T3/T4 execution only."""
    if not _SAFE_EXECUTION_ID.fullmatch(execution_id):
        raise ValueError("execution_id is not safe for a checkpoint filename")
    if not _SAFE_EXECUTION_ID.fullmatch(step_id):
        raise ValueError("step_id is not safe for a checkpoint filename")
    if execution_lane not in _GOVERNED_LANES:
        raise ValueError("durable step checkpoints are restricted to T3/T4")
    if not _SHA256.fullmatch(input_fingerprint) or not _SHA256.fullmatch(idempotency_key):
        raise ValueError("step fingerprints must be lowercase SHA-256 digests")
    if status not in _STEP_STATUSES or pending_write_status not in _PENDING_WRITE_STATUSES:
        raise ValueError("step checkpoint status is invalid")
    if output_reference is not None and (not isinstance(output_reference, str) or not output_reference):
        raise ValueError("output_reference must be null or a nonempty string")
    if not isinstance(next_safe_action, str) or not next_safe_action:
        raise ValueError("next_safe_action is required")

    checkpoint_root = root / "step-checkpoints" / execution_id
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    path = checkpoint_root / f"{step_id}.json"
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("existing step checkpoint is unreadable") from exc
        if previous.get("idempotency_key") != idempotency_key:
            raise ValueError("step checkpoint idempotency mismatch")
        created_at = str(previous.get("created_at", created_at))
    value = {
        "schema_version": "1.0",
        "execution_id": execution_id,
        "execution_lane": execution_lane,
        "step_id": step_id,
        "created_at": created_at,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_fingerprint": input_fingerprint,
        "idempotency_key": idempotency_key,
        "status": status,
        "pending_write_status": pending_write_status,
        "output_reference": output_reference,
        "evidence": evidence,
        "resumable": status != "completed",
        "next_safe_action": next_safe_action,
    }
    return _atomic_json(path, value)


MappingLike = dict[str, Any]
