"""Compact task-scoped authorization records shared across execution legs."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1.0"
RED_ACTIONS = (
    "credentials_and_identity",
    "destructive_or_irreversible_changes",
    "external_or_production_writes",
    "billing",
    "git_history_changes",
    "undeclared_scope",
)

_DRIVE_PATH = re.compile(r"^[A-Za-z]:/")


def _normalized_scope(declared_paths: Sequence[str]) -> list[str]:
    scope: set[str] = set()
    for raw_path in declared_paths:
        path = str(raw_path).replace("\\", "/").strip()
        while path.startswith("./"):
            path = path[2:]
        if not path or path.startswith("/") or _DRIVE_PATH.match(path):
            raise ValueError("authorization scope must use non-empty workspace-relative paths")
        if any(part == ".." for part in path.split("/")):
            raise ValueError("authorization scope cannot escape the workspace")
        scope.add(path.rstrip("/"))
    return sorted(scope)


def _record_fingerprint(record: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in record.items() if key != "authorization_sha256"}
    return hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def make_task_authorization(
    *, task: str, project: str, declared_paths: Sequence[str], profile: str,
    autonomy: Mapping[str, Any], policy_revision: str = "governance-continuity-v1",
    task_id: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic authorization record without retaining task text."""
    scope = _normalized_scope(declared_paths)
    task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()
    stable_id = task_id or hashlib.sha256(
        json.dumps({"project": project, "task": task_hash, "scope": scope}, sort_keys=True).encode()
    ).hexdigest()[:32]
    record = {
        "schema_version": SCHEMA_VERSION,
        "task_id": stable_id,
        "task_sha256": task_hash,
        "project": project,
        "declared_scope": scope,
        "autonomy_profile": str(autonomy.get("profile", profile)),
        "policy_revision": policy_revision,
        "approved_yellow_actions": "task-scoped-in-scope-only",
        "expiry": "task-completion-or-material-change",
        "material_change_invalidators": [
            "scope", "target", "policy_revision", "external_side_effect", "identity",
        ],
        "red_action_categories": list(RED_ACTIONS),
        "intent_reason": str(autonomy.get("reason", "NO_OPERATIVE_AUTONOMOUS_INTENT")),
        "intent_signal": autonomy.get("signal"),
    }
    record["authorization_sha256"] = _record_fingerprint(record)
    return record


def authorization_matches(record: Mapping[str, Any], *, project: str,
                          declared_paths: Sequence[str], policy_revision: str) -> bool:
    """Validate continuity without widening scope or replaying another task."""
    if not isinstance(record, Mapping) or record.get("schema_version") != SCHEMA_VERSION:
        return False
    if record.get("project") != project or record.get("policy_revision") != policy_revision:
        return False
    try:
        expected_scope = _normalized_scope(declared_paths)
    except ValueError:
        return False
    fingerprint = record.get("authorization_sha256")
    return (
        record.get("declared_scope") == expected_scope
        and isinstance(fingerprint, str)
        and fingerprint == _record_fingerprint(record)
    )
