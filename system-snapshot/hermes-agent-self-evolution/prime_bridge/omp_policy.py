"""Closed OMP admission graph and sanitized instruction bundle for Prime code jobs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from prime_bridge.capability_policy import CREDENTIAL_SHAPE, FORBIDDEN_KEY


class OmpPolicyError(RuntimeError):
    pass


DECISIONS = frozenset({"ALLOW_LSP", "ALLOW_HASH_PREVIEW", "ALLOW_WORKER", "DENY_NATIVE_FALLBACK"})
DENY_EDGES = frozenset({
    "direct_omp", "nested_omp", "omp_to_prime", "omp_to_hermes", "subagent",
    "missing_stale_parent", "disabled_profile_global",
})
_PROFILE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class OmpCodingPolicy:
    enabled: bool
    decisions: Mapping[str, dict[str, str]]
    deny: Mapping[str, bool]
    shared_budget: bool
    require_ready_feasibility_hash: bool
    fingerprint: str

    @classmethod
    def load(cls, path: Path) -> "OmpCodingPolicy":
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OmpPolicyError("OMP coding policy is unavailable") from exc
        expected = {"schema_version", "enabled", "decisions", "deny", "shared_budget", "require_ready_feasibility_hash"}
        if not isinstance(value, dict) or set(value) != expected or value.get("schema_version") != "1.0":
            raise OmpPolicyError("OMP coding policy keys are not closed")
        if type(value["enabled"]) is not bool or type(value["shared_budget"]) is not bool or type(value["require_ready_feasibility_hash"]) is not bool:
            raise OmpPolicyError("OMP coding policy booleans are invalid")
        decisions = value["decisions"]
        if not isinstance(decisions, dict) or set(decisions) != DECISIONS:
            raise OmpPolicyError("OMP decisions are not closed")
        for name, item in decisions.items():
            if not isinstance(item, dict) or set(item) != {"classification", "approval", "description"}:
                raise OmpPolicyError("OMP decision is invalid")
            if item["classification"] not in {"retrieval", "action"} or item["approval"] not in {"auto", "required"}:
                raise OmpPolicyError("OMP decision classification/approval is invalid")
            if item["classification"] == "retrieval" and item["approval"] != "auto":
                raise OmpPolicyError("OMP retrieval must be auto-approved")
        deny = value["deny"]
        if not isinstance(deny, dict) or set(deny) != DENY_EDGES or any(type(v) is not bool for v in deny.values()):
            raise OmpPolicyError("OMP deny edges are not closed")
        if not all(deny[edge] for edge in DENY_EDGES):
            raise OmpPolicyError("OMP forbidden edges must all be closed")
        return cls(
            enabled=value["enabled"], decisions=decisions, deny=deny,
            shared_budget=value["shared_budget"],
            require_ready_feasibility_hash=value["require_ready_feasibility_hash"],
            fingerprint=hashlib.sha256(raw).hexdigest(),
        )

    def decision(self, name: str) -> dict[str, str]:
        if not self.enabled:
            raise OmpPolicyError("OMP coding is disabled")
        try:
            return self.decisions[name]
        except (KeyError, TypeError) as exc:
            raise OmpPolicyError("OMP decision is denied") from exc


def _require_hex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise OmpPolicyError(f"{label} is not a valid hash")
    return value


def admit_omp(
    *,
    feasibility: Mapping[str, Any],
    prime_job: Mapping[str, Any],
    project_root: Path,
    profile: str,
    model_route: Mapping[str, Any],
    capability_policy: Mapping[str, Any],
    omp_policy: OmpCodingPolicy,
    shared_budget: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an OMP admission receipt or raise OmpPolicyError (fail closed)."""
    if not omp_policy.enabled:
        raise OmpPolicyError("OMP coding is disabled globally")
    if not _PROFILE.fullmatch(profile):
        raise OmpPolicyError("profile is not canonical")
    if omp_policy.require_ready_feasibility_hash:
        verdict = feasibility.get("verdict")
        if verdict != "READY":
            raise OmpPolicyError("OMP feasibility is not READY")
        _require_hex(feasibility.get("captured_at"), re.compile(r"^.+$"), "feasibility captured_at")
    # Prime job receipt must be present and non-terminal.
    if not isinstance(prime_job, Mapping) or prime_job.get("status") not in {"starting", "running"}:
        raise OmpPolicyError("missing or stale Prime parent job")
    parent_id = prime_job.get("job_id")
    if not isinstance(parent_id, str) or not re.fullmatch(r"[0-9a-f]{32}", parent_id):
        raise OmpPolicyError("invalid Prime parent job id")
    # Project root must be a real directory.
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise OmpPolicyError("project root is not a directory")
    # Model route must be Hermes-derived and not a fallback.
    if not isinstance(model_route, Mapping) or model_route.get("status") != "READY":
        raise OmpPolicyError("Hermes model route is not READY")
    if model_route.get("explicit_fallback") is not None:
        raise OmpPolicyError("OMP must not use an explicit model fallback")
    # Capability policy must be enabled.
    if not isinstance(capability_policy, Mapping) or capability_policy.get("enabled") is not True:
        raise OmpPolicyError("capability policy is disabled")
    # Shared budget must be present.
    if not isinstance(shared_budget, Mapping) or shared_budget.get("status") != "READY":
        raise OmpPolicyError("shared admission budget is not READY")
    # Deny edges are enforced by the policy loader; re-assert here for defense in depth.
    for edge in DENY_EDGES:
        if not omp_policy.deny[edge]:
            raise OmpPolicyError(f"forbidden edge is open: {edge}")
    return {
        "status": "ADMITTED",
        "parent_prime_job_id": parent_id,
        "project_root": str(root),
        "profile": profile,
        "model_route_fingerprint": model_route.get("route", {}).get("fingerprint"),
        "omp_policy_fingerprint": omp_policy.fingerprint,
        "shared_budget": True,
    }


def build_instruction_bundle(
    *,
    project_root: Path,
    approved_rules: Mapping[str, Any],
    source_hash: str,
) -> dict[str, Any]:
    """Build a sanitized, hash-bound instruction bundle from Hermes-approved rules.

    Removes secrets and external paths, binds to project/source hashes, and
    never allows OMP file discovery.
    """
    _require_hex(source_hash, _HEX64, "source hash")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise OmpPolicyError("project root is not a directory")
    if not isinstance(approved_rules, Mapping):
        raise OmpPolicyError("approved rules must be an object")

    def clean(value: Any, key: str | None = None) -> Any:
        if key is not None and FORBIDDEN_KEY.search(str(key)):
            return "[redacted]"
        if isinstance(value, dict):
            return {str(child_key): clean(child, str(child_key)) for child_key, child in value.items()}
        if isinstance(value, list):
            return [clean(child) for child in value]
        if isinstance(value, str):
            if CREDENTIAL_SHAPE.search(value):
                return "[redacted]"
            # Strip absolute external paths; keep only project-relative references.
            if re.search(r"(?:[A-Za-z]:[\\/]|/mnt/[A-Za-z]/|/home/[^/]+/)", value):
                return "[external-path-redacted]"
        return value

    sanitized = clean(approved_rules)
    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    bundle_hash = hashlib.sha256(encoded).hexdigest()
    return {
        "schema_version": "1.0",
        "project_root": str(root),
        "source_hash": source_hash,
        "rules": sanitized,
        "bundle_hash": bundle_hash,
        "discovery": "disabled",
    }
