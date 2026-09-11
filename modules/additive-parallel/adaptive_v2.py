#!/usr/bin/env python3
"""Provider-free portable projection of adaptive v2 decision contracts.

This module computes facts only. It never launches workers or reviewers, changes a
recipient's S pipeline, calls a provider, activates a profile, or publishes a result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping


MODULE = Path(__file__).resolve().parent
POLICY_PATH = MODULE / "adaptive-v2.policy.json"
REVIEW_PATH = MODULE / "adaptive-v2.review.json"
CATALOG_PATH = MODULE / "adaptive-v2.catalog.json"

ACTIVATION_STATES = {"inactive", "shadow", "canary", "pilot_limited", "pilot_wider", "review", "production"}
QUALIFICATION_STATES = {"disabled", "candidate", "canary_qualified", "production_qualified"}
CAPABILITIES = {"read_only", "isolated_coding", "review"}
GRANT_IDS = {
    "execution_parallelism", "reviewer_concurrency", "career_kernel",
    "temperament", "career_shadow_pass",
}
GRANT_ROLLBACK_TARGETS = {
    "execution_parallelism": "inactive",
    "reviewer_concurrency": "serial",
    "career_kernel": "neutral",
    "temperament": "disabled",
    "career_shadow_pass": "disabled",
}
_RECORD_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/|@+\-]{0,511}\Z")
PIPELINES = {"S0", "S1", "S2", "S3", "S4"}
CAREER_STATES = {
    "candidate": {"canary"},
    "canary_qualified": {"canary", "pilot"},
    "production_qualified": {"canary", "pilot", "production"},
    "disabled": set(),
}
REASON_ORDER = (
    "disabled", "expired", "incompatible_contract", "method_overlap",
    "insufficient_calls", "no_qualified_pairing",
)
EXPECTED_CAREERS = {
    "anthropologist", "chaos_mathematician", "economist", "engineer",
    "evolutionary_biologist", "game_theorist", "linguist", "mythologist",
    "phenomenologist", "physicist", "psychoanalyst", "structuralist",
}


class AdaptiveV2Error(ValueError):
    """Raised when an adaptive v2 input is malformed or over-authoritative."""


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AdaptiveV2Error(f"{path.name} must contain an object")
    return value


def _closed(value: Mapping[str, Any], expected: set[str], label: str, *, optional: set[str] | None = None) -> None:
    if not isinstance(value, Mapping):
        raise AdaptiveV2Error(f"{label} must be an object")
    optional = optional or set()
    if set(value) - expected - optional:
        raise AdaptiveV2Error(f"{label} contains unknown fields")
    if expected - set(value):
        raise AdaptiveV2Error(f"{label} is missing required fields")


def _integer(value: Any, label: str, minimum: int = 0, maximum: int = 1_000_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AdaptiveV2Error(f"{label} must be an integer between {minimum} and {maximum}")
    return value


def _strings(value: Any, label: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise AdaptiveV2Error(f"{label} must be a string array")
    if len(value) != len(set(value)) or nonempty and not value:
        raise AdaptiveV2Error(f"{label} must contain unique values")
    return tuple(value)


def _utc(value: str, label: str) -> datetime:
    if not isinstance(value, str):
        raise AdaptiveV2Error(f"{label} must be UTC")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdaptiveV2Error(f"{label} must be UTC") from exc
    if result.tzinfo is None or result.utcoffset() != timezone.utc.utcoffset(result):
        raise AdaptiveV2Error(f"{label} must be UTC")
    return result


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise AdaptiveV2Error(f"{label} must be a lowercase SHA-256 digest")
    return value


def _path(value: str, label: str) -> str:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise AdaptiveV2Error(f"{label} contains an unsafe path")
    return candidate.as_posix()


def _workspace_root(value: Any) -> str:
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise AdaptiveV2Error("workspace_root must be an absolute canonical path")
    supplied = os.path.normpath(os.path.abspath(value))
    canonical = os.path.normpath(os.path.realpath(supplied))
    if supplied.casefold() != canonical.casefold():
        raise AdaptiveV2Error("workspace_root must be an absolute canonical path")
    return canonical


def _workspace_path(root: str, value: str, label: str) -> str:
    relative = _path(value, label)
    lexical = os.path.normpath(os.path.abspath(os.path.join(root, relative)))
    resolved = os.path.normpath(os.path.realpath(lexical))
    try:
        lexical_within = os.path.commonpath((root, lexical)).casefold() == root.casefold()
        resolved_within = os.path.commonpath((root, resolved)).casefold() == root.casefold()
    except ValueError:
        lexical_within = resolved_within = False
    if not lexical_within or not resolved_within:
        raise AdaptiveV2Error(f"{label} escapes workspace_root")
    return relative


def _validate_grant(grant: Mapping[str, Any], grant_id: str, *, shipped: bool) -> None:
    fields = {"enabled", "state", "qualification_reference", "expiry", "rollback_target"}
    _closed(grant, fields, f"grant {grant_id}")
    if type(grant["enabled"]) is not bool or grant["state"] not in QUALIFICATION_STATES:
        raise AdaptiveV2Error(f"grant {grant_id} is invalid")
    reference = grant["qualification_reference"]
    if reference is not None and (
        not isinstance(reference, str)
        or not (re.fullmatch(r"[0-9a-f]{64}", reference) or _RECORD_KEY.fullmatch(reference))
    ):
        raise AdaptiveV2Error(f"grant {grant_id}.qualification_reference is invalid")
    if grant["expiry"] is not None:
        _utc(grant["expiry"], f"grant {grant_id}.expiry")
    if grant["rollback_target"] != GRANT_ROLLBACK_TARGETS[grant_id]:
        raise AdaptiveV2Error(f"grant {grant_id}.rollback_target is invalid")
    if grant["enabled"] and grant["state"] == "disabled":
        raise AdaptiveV2Error(f"grant {grant_id} cannot enable a disabled qualification")
    if grant["enabled"] and reference is None:
        raise AdaptiveV2Error(f"grant {grant_id} requires a qualification reference when enabled")
    if grant["enabled"] and grant["expiry"] is None:
        raise AdaptiveV2Error(f"grant {grant_id} requires an expiry when enabled")
    if shipped and grant["enabled"]:
        raise AdaptiveV2Error("shipped grants must be disabled")


def validate_policy(policy: Mapping[str, Any], *, shipped: bool = False) -> None:
    required = {"schema_version", "strategy", "activation_state", "execution", "reviewer", "grants", "scaling"}
    _closed(policy, required, "policy", optional={"$schema"})
    if policy["schema_version"] != "2.0" or policy["strategy"] not in {"serial", "fixed_two", "adaptive"}:
        raise AdaptiveV2Error("unsupported policy identity")
    if policy["activation_state"] not in ACTIVATION_STATES:
        raise AdaptiveV2Error("invalid activation state")
    if shipped and policy["activation_state"] != "inactive":
        raise AdaptiveV2Error("shipped adaptive v2 policy must remain inactive")
    layer_fields = {"enabled", "capability", "qualification_state", "initial_ceiling", "qualified_ceiling", "desired_ceiling"}
    for name in ("execution", "reviewer"):
        layer = policy[name]
        _closed(layer, layer_fields, name)
        if type(layer["enabled"]) is not bool or layer["capability"] not in CAPABILITIES or layer["qualification_state"] not in QUALIFICATION_STATES:
            raise AdaptiveV2Error(f"{name} layer is invalid")
        initial = _integer(layer["initial_ceiling"], f"{name}.initial_ceiling", maximum=2)
        qualified = _integer(layer["qualified_ceiling"], f"{name}.qualified_ceiling", maximum=8)
        desired = _integer(layer["desired_ceiling"], f"{name}.desired_ceiling", maximum=8)
        if not initial <= qualified <= desired:
            raise AdaptiveV2Error(f"{name} ceilings must be monotonic")
        if shipped and layer["enabled"]:
            raise AdaptiveV2Error(f"shipped {name} layer must be disabled")
    grants = policy["grants"]
    if not isinstance(grants, Mapping) or set(grants) != GRANT_IDS:
        raise AdaptiveV2Error("grants must contain exactly the five approved grant ids")
    for grant_id in sorted(GRANT_IDS):
        _validate_grant(grants[grant_id], grant_id, shipped=shipped)
    if grants["execution_parallelism"]["enabled"] != policy["execution"]["enabled"]:
        raise AdaptiveV2Error("execution_parallelism grant must match execution.enabled")
    if grants["reviewer_concurrency"]["enabled"] != policy["reviewer"]["enabled"]:
        raise AdaptiveV2Error("reviewer_concurrency grant must match reviewer.enabled")
    if grants["career_kernel"]["enabled"] and not grants["reviewer_concurrency"]["enabled"]:
        raise AdaptiveV2Error("career_kernel requires an enabled reviewer_concurrency grant")
    for modifier_id in ("temperament", "career_shadow_pass"):
        if grants[modifier_id]["enabled"] and not grants["career_kernel"]["enabled"]:
            raise AdaptiveV2Error(f"{modifier_id} requires an enabled career_kernel grant")
    scaling = policy["scaling"]
    _closed(scaling, {"promotion_streak", "demotion_streak", "cooldown_seconds", "backlog_per_worker"}, "scaling")
    _integer(scaling["promotion_streak"], "promotion_streak", 1, 100)
    _integer(scaling["demotion_streak"], "demotion_streak", 1, 100)
    _integer(scaling["cooldown_seconds"], "cooldown_seconds", 0, 86400)
    _integer(scaling["backlog_per_worker"], "backlog_per_worker", 1, 100)


def policy_sha256(policy: Mapping[str, Any]) -> str:
    validate_policy(policy)
    canonical = {key: policy[key] for key in ("schema_version", "strategy", "activation_state", "execution", "reviewer", "scaling")}
    canonical["grants"] = {grant_id: policy["grants"][grant_id] for grant_id in sorted(GRANT_IDS)}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def decide_admission(policy: Mapping[str, Any], facts: Mapping[str, Any], *, layer: str, now: datetime) -> dict[str, Any]:
    """Match the canonical execution admission decision without performing effects."""
    validate_policy(policy)
    if layer not in {"execution", "reviewer"}:
        raise AdaptiveV2Error("layer is invalid")
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
        raise AdaptiveV2Error("now must be UTC")
    fields = {"ready_count", "running_count", "host_capacity", "parent_reserve", "reviewer_reserve", "verified_streak", "adverse_streak", "backlog_count", "cooldown_until", "trusted_feedback_sha256", "lineage_sha256", "candidate_canary_authorized"}
    _closed(facts, fields, "admission facts")
    for field in fields - {"cooldown_until", "trusted_feedback_sha256", "lineage_sha256", "candidate_canary_authorized"}:
        _integer(facts[field], field)
    if facts["cooldown_until"] is not None:
        _utc(facts["cooldown_until"], "cooldown_until")
    digest = facts["trusted_feedback_sha256"]
    if digest is not None:
        _sha256(digest, "trusted_feedback_sha256")
    _sha256(facts["lineage_sha256"], "lineage_sha256")
    if type(facts["candidate_canary_authorized"]) is not bool:
        raise AdaptiveV2Error("candidate_canary_authorized must be boolean")
    selected = policy[layer]
    grant_id = "execution_parallelism" if layer == "execution" else "reviewer_concurrency"
    grant = policy["grants"][grant_id]
    reasons: list[str] = []
    if policy["activation_state"] == "inactive" or not selected["enabled"]:
        desired = admitted = 0
        reasons.append("INACTIVE")
    elif not grant["enabled"]:
        desired = admitted = 0
        reasons.append("GRANT_UNQUALIFIED")
    elif grant["expiry"] is not None and now >= _utc(grant["expiry"], f"{grant_id}.expiry"):
        desired = admitted = 0
        reasons.append("GRANT_EXPIRED")
    elif not isinstance(grant["qualification_reference"], str) or re.fullmatch(r"[0-9a-f]{64}", grant["qualification_reference"]) is None:
        desired = admitted = 0
        reasons.append("GRANT_UNQUALIFIED")
    elif policy["activation_state"] == "review" and layer == "execution":
        desired = admitted = 0
        reasons.append("ACTIVATION_SCOPE")
    elif policy["activation_state"] == "canary" and (grant["state"] != "candidate" or not facts["candidate_canary_authorized"]):
        desired = admitted = 0
        reasons.append("CANDIDATE_CANARY_UNAUTHORIZED")
    elif (
        policy["activation_state"] == "production" and grant["state"] != "production_qualified"
    ) or (
        policy["activation_state"] in {"review", "pilot_limited", "pilot_wider"}
        and grant["state"] not in {"canary_qualified", "production_qualified"}
    ) or (
        policy["activation_state"] == "shadow"
        and grant["state"] not in {"candidate", "canary_qualified", "production_qualified"}
    ):
        desired = admitted = 0
        reasons.append("GRANT_UNQUALIFIED")
    elif facts["ready_count"] == 0:
        desired = admitted = 0
        reasons.append("NO_READY_WORK")
    elif selected["qualification_state"] == "disabled":
        desired = admitted = 0
        reasons.append("CAPABILITY_UNQUALIFIED")
    else:
        qualification_ceiling = min(selected["desired_ceiling"], selected["qualified_ceiling"])
        if policy["strategy"] == "serial":
            ceiling = min(qualification_ceiling, 1)
        elif policy["strategy"] == "fixed_two":
            ceiling = min(qualification_ceiling, 2)
        else:
            ceiling = min(selected["initial_ceiling"], qualification_ceiling)
            completed_streaks = facts["verified_streak"] // policy["scaling"]["promotion_streak"]
            for tier in (1, 2, 4, 8):
                if ceiling < tier <= qualification_ceiling:
                    if completed_streaks == 0:
                        break
                    ceiling = tier
                    completed_streaks -= 1
        if policy["activation_state"] in {"shadow", "review"}:
            ceiling = min(ceiling, selected["initial_ceiling"])
        pressure = max(1, (facts["backlog_count"] + policy["scaling"]["backlog_per_worker"] - 1) // policy["scaling"]["backlog_per_worker"])
        desired = min(facts["ready_count"], ceiling, pressure)
        if facts["adverse_streak"] >= policy["scaling"]["demotion_streak"]:
            desired = min(desired, 1)
            reasons.append("ADVERSE_FEEDBACK")
        if facts["cooldown_until"] is not None and now < _utc(facts["cooldown_until"], "cooldown_until"):
            desired = min(desired, 1)
            reasons.append("COOLDOWN_ACTIVE")
        available = max(0, facts["host_capacity"] - facts["parent_reserve"] - facts["reviewer_reserve"] - facts["running_count"])
        admitted = min(desired, available)
        rollout_fraction = {"canary": .10, "pilot_limited": .10, "pilot_wider": .50}.get(policy["activation_state"])
        if rollout_fraction is not None:
            bucket = int(facts["lineage_sha256"][:8], 16) / 0x100000000
            if bucket >= rollout_fraction:
                admitted = 0
                reasons.append("PILOT_NOT_SELECTED")
        if policy["activation_state"] == "shadow":
            admitted = 0
            reasons.append("SHADOW_OBSERVATION_ONLY")
        if selected["qualified_ceiling"] < selected["desired_ceiling"]:
            reasons.append("QUALIFICATION_CEILING")
        if facts["parent_reserve"]:
            reasons.append("PARENT_CAPACITY_RESERVED")
        if facts["reviewer_reserve"]:
            reasons.append("REVIEW_CAPACITY_RESERVED")
        if admitted < desired and policy["activation_state"] != "shadow":
            reasons.append("HOST_CAPACITY")
        if not reasons:
            reasons.append("ADMITTED")
    return {
        "schema_version": "2.0", "layer": layer, "policy_sha256": policy_sha256(policy),
        "desired_count": desired, "admitted_count": admitted,
        "running_count": facts["running_count"], "observed_ready_count": facts["ready_count"],
        "limiting_reasons": list(dict.fromkeys(reasons)),
        "decided_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trusted_feedback_sha256": digest,
    }


def validate_work_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the canonical workspace/resource packet contract."""
    fields = {
        "schema_version", "packet_id", "node_id", "workspace_root", "read_set",
        "write_set", "resource_set", "resource_state", "dependencies",
        "base_revision", "expected_artifacts", "assertions", "budget",
        "route_reference", "goal_reference",
    }
    _closed(packet, fields, "work packet")
    if packet["schema_version"] != "2.0":
        raise AdaptiveV2Error("unsupported work packet version")
    for field in ("packet_id", "node_id", "route_reference", "goal_reference"):
        if not isinstance(packet[field], str) or not packet[field]:
            raise AdaptiveV2Error(f"work packet {field} is required")
    root = _workspace_root(packet["workspace_root"])
    normalized = dict(packet)
    normalized["workspace_root"] = root
    for field in ("read_set", "write_set", "expected_artifacts"):
        normalized[field] = tuple(
            _workspace_path(root, path, field) for path in _strings(packet[field], field)
        )
    normalized["resource_set"] = _strings(packet["resource_set"], "resource_set")
    normalized["dependencies"] = _strings(packet["dependencies"], "dependencies")
    normalized["assertions"] = _strings(packet["assertions"], "assertions", nonempty=True)
    if packet["resource_state"] not in {"complete", "unknown"}:
        raise AdaptiveV2Error("resource_state is invalid")
    if packet["resource_state"] == "complete" and not normalized["resource_set"]:
        raise AdaptiveV2Error("complete resource facts require a nonempty resource_set")
    if packet["resource_state"] == "unknown" and normalized["resource_set"]:
        raise AdaptiveV2Error("unknown resource facts require an empty resource_set")
    _sha256(packet["base_revision"], "base_revision")
    budget_fields = {"provider_calls", "tokens", "wall_seconds", "cost"}
    _closed(packet["budget"], budget_fields, "budget")
    _integer(packet["budget"]["provider_calls"], "budget.provider_calls", maximum=1000)
    _integer(packet["budget"]["tokens"], "budget.tokens", maximum=10_000_000)
    for field in ("wall_seconds", "cost"):
        value = packet["budget"][field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise AdaptiveV2Error(f"budget.{field} must be nonnegative numeric")
    return normalized


def packets_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Conservatively detect canonical path, root, resource, and dependency overlap."""
    left = validate_work_packet(left)
    right = validate_work_packet(right)

    def root_is_strict_ancestor(left_root: str, right_root: str) -> bool:
        try:
            common = os.path.commonpath((left_root, right_root)).casefold()
        except ValueError:
            return False
        return left_root.casefold() != right_root.casefold() and common in {left_root.casefold(), right_root.casefold()}

    def aliases(packet: Mapping[str, Any], path: str) -> tuple[tuple[str, ...], ...]:
        lexical = os.path.normpath(os.path.abspath(os.path.join(packet["workspace_root"], path))).casefold()
        resolved = os.path.normpath(os.path.realpath(lexical)).casefold()
        return tuple(sorted({
            tuple(part for part in value.replace("\\", "/").split("/") if part)
            for value in (lexical, resolved)
        }))

    def paths_overlap(left_path: str, right_path: str) -> bool:
        for left_alias in aliases(left, left_path):
            for right_alias in aliases(right, right_path):
                shared = min(len(left_alias), len(right_alias))
                if left_alias[:shared] == right_alias[:shared]:
                    return True
        return False

    left_writes = left["write_set"] + left["expected_artifacts"]
    right_writes = right["write_set"] + right["expected_artifacts"]
    path_conflict = any(
        paths_overlap(left_path, right_path)
        for left_path in left_writes for right_path in right_writes + right["read_set"]
    ) or any(
        paths_overlap(right_path, left_path)
        for right_path in right_writes for left_path in left["read_set"]
    )
    return bool(
        left["resource_state"] == "unknown" or right["resource_state"] == "unknown"
        or root_is_strict_ancestor(left["workspace_root"], right["workspace_root"])
        or path_conflict
        or set(left["resource_set"]) & set(right["resource_set"])
        or left["node_id"] in right["dependencies"]
        or right["node_id"] in left["dependencies"]
    )
def validate_review_template(template: Mapping[str, Any], *, shipped: bool = False) -> None:
    fields = {"schema", "activation_state", "selector_owner", "completion_owner", "max_model_calls", "initial_ceiling", "max_concurrent_reviewers", "grants", "provider_calls_for_selection", "dispatch_authority", "completion_authority"}
    _closed(template, fields, "review template")
    if template["schema"] != "lapart.adaptive-review-template.v2" or template["activation_state"] not in ACTIVATION_STATES:
        raise AdaptiveV2Error("review template identity is invalid")
    if shipped and template["activation_state"] != "inactive":
        raise AdaptiveV2Error("shipped review template must remain inactive")
    if template["selector_owner"] != "recipient-s-pipeline" or template["completion_owner"] != "recipient-review-controller":
        raise AdaptiveV2Error("recipient review ownership changed")
    expected_levels = {"S0", "S1", "S2", "S3", "S4"}
    for field in ("max_model_calls", "initial_ceiling", "max_concurrent_reviewers"):
        if set(template[field]) != expected_levels or any(type(value) is not int or value < 0 for value in template[field].values()):
            raise AdaptiveV2Error(f"{field} is invalid")
    if template["max_model_calls"] != {"S0": 0, "S1": 1, "S2": 3, "S3": 5, "S4": 7}:
        raise AdaptiveV2Error("S pipeline call budgets changed")
    grants = template["grants"]
    if not isinstance(grants, Mapping) or set(grants) != GRANT_IDS:
        raise AdaptiveV2Error("layer grants are incomplete")
    for grant_id in sorted(GRANT_IDS):
        _validate_grant(grants[grant_id], grant_id, shipped=shipped)
    if grants["career_kernel"]["enabled"] and not grants["reviewer_concurrency"]["enabled"]:
        raise AdaptiveV2Error("career_kernel requires an enabled reviewer_concurrency grant")
    for modifier_id in ("temperament", "career_shadow_pass"):
        if grants[modifier_id]["enabled"] and not grants["career_kernel"]["enabled"]:
            raise AdaptiveV2Error(f"{modifier_id} requires an enabled career_kernel grant")
    if template["provider_calls_for_selection"] != 0 or template["dispatch_authority"] is not False or template["completion_authority"] is not False:
        raise AdaptiveV2Error("portable adapter acquired authority")


def plan_review_stage(template: Mapping[str, Any], *, pipeline: str, selected_members: Iterable[str], max_model_calls: int, host_slots: int | None = None, execution_slots: int = 0, convergence_reserve: int = 1, qualification_verified: bool = False, lineage_sha256: str | None = None) -> dict[str, Any]:
    validate_review_template(template)
    if pipeline not in PIPELINES:
        raise AdaptiveV2Error("unknown S pipeline")
    members = tuple(selected_members)
    if len(members) != len(set(members)) or any(not isinstance(item, str) or not item for item in members):
        raise AdaptiveV2Error("review stage members must be unique identities")
    _integer(max_model_calls, "max_model_calls")
    _integer(execution_slots, "execution_slots")
    _integer(convergence_reserve, "convergence_reserve")
    if host_slots is not None:
        _integer(host_slots, "host_slots")
    activation = template["activation_state"]
    if pipeline == "S0":
        width = 0
    elif activation in {"inactive", "shadow"}:
        width = 1 if members else 0
    elif qualification_verified is not True:
        width = 1 if members else 0
    else:
        ceiling = template["max_concurrent_reviewers"][pipeline]
        initial = template["initial_ceiling"][pipeline]
        qualified = ceiling if activation in {"pilot_wider", "production"} else initial
        if activation in {"pilot_limited", "pilot_wider"}:
            if not isinstance(lineage_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", lineage_sha256) is None:
                qualified = 1
            else:
                fraction = .10 if activation == "pilot_limited" else .50
                if int(lineage_sha256[:8], 16) / 0x100000000 >= fraction:
                    qualified = 1
        capacity = qualified if host_slots is None else max(0, host_slots - execution_slots - convergence_reserve)
        width = min(len(members), max_model_calls, qualified, capacity)
    if members and width == 0:
        reason, batches = "REVIEW_CAPACITY_RESERVED", ()
    else:
        reason = "SERIAL_COMPATIBILITY" if width <= 1 else "ADAPTIVE_REVIEW"
        batches = tuple(members[index:index + width] for index in range(0, len(members), max(width, 1)))
    grants = {
        member: hashlib.sha256(json.dumps({
            "pipeline": pipeline, "activation_state": activation,
            "selected_members": members, "member": member, "concurrency": width,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for member in members
    }
    return {
        "pipeline": pipeline, "activation_state": activation, "selected_members": list(members),
        "concurrency": width, "batches": [list(batch) for batch in batches],
        "reason_code": reason, "grant_sha256s": dict(sorted(grants.items())),
    }


def validate_catalog(catalog: Mapping[str, Any]) -> None:
    fields = {"schema_version", "reviewers", "lens_matrix", "reviewer_bindings", "desired_distinct_careers", "method_tags", "compatibility", "careers"}
    _closed(catalog, fields, "catalog")
    if catalog["schema_version"] != "2.0" or not isinstance(catalog["reviewers"], list) or not isinstance(catalog["careers"], list):
        raise AdaptiveV2Error("catalog identity is invalid")
    if catalog["desired_distinct_careers"] != {"S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4}:
        raise AdaptiveV2Error("desired career counts changed")
    careers = catalog["careers"]
    ids = [item.get("career_id") for item in careers if isinstance(item, Mapping)]
    if len(ids) != 12 or set(ids) != EXPECTED_CAREERS or len(ids) != len(set(ids)):
        raise AdaptiveV2Error("catalog must contain exactly twelve approved careers")
    if set(catalog["method_tags"]) != EXPECTED_CAREERS:
        raise AdaptiveV2Error("career method tags are incomplete")
    if set(catalog["compatibility"]) != set(catalog["lens_matrix"]):
        raise AdaptiveV2Error("compatibility must exactly cover the lens matrix")
    forbidden = {"team", "teams", "vote", "votes", "voting", "quorum", "winner"}
    for item in careers:
        required = {"career_id", "primary_method", "source_path", "source_sha256", "career_method", "lunar_method", "temperament", "career_shadow", "qualification_state"}
        _closed(item, required, "career")
        if forbidden & set(item):
            raise AdaptiveV2Error("Council team or voting behavior is not portable")
        career_id = item["career_id"]
        expected_path = f"skills-shared/brainstorm-council/agents/bc-{career_id.replace('_', '-')}.md"
        if item["source_path"] != expected_path or catalog["method_tags"].get(career_id) != item["primary_method"]:
            raise AdaptiveV2Error("career source or method projection changed")
        digest = item["source_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise AdaptiveV2Error("career source identity is invalid")
        temperament, shadow = item["temperament"], item["career_shadow"]
        _closed(temperament, {"temperament_id", "strengths", "biases"}, "temperament")
        _closed(shadow, {"shadow_id", "countercheck", "dispositions"}, "career shadow")
        if _strings(shadow["dispositions"], "shadow dispositions", nonempty=True) != ("accepted", "rejected", "unresolved"):
            raise AdaptiveV2Error("career Shadow disposition contract changed")
    if any(career not in EXPECTED_CAREERS for values in catalog["compatibility"].values() for career in values):
        raise AdaptiveV2Error("compatibility references an unknown career")


def method_distance(catalog: Mapping[str, Any], left: str, right: str) -> int:
    validate_catalog(catalog)
    if left not in EXPECTED_CAREERS or right not in EXPECTED_CAREERS:
        raise AdaptiveV2Error("unknown career")
    return int(catalog["method_tags"][left] != catalog["method_tags"][right])


def _ordered_reasons(values: Iterable[str]) -> tuple[str, ...]:
    present = set(values)
    return tuple(reason for reason in REASON_ORDER if reason in present)


def assign_careers(catalog: Mapping[str, Any], *, pipeline: str, calls: Iterable[Mapping[str, Any]], qualifications: Iterable[Mapping[str, Any]], purpose: str = "production", career_enabled: bool = True, candidate_canary_authorized: bool = False, now_epoch: int = 0, model_route: str = "default", workload_class: str = "default", layer_configuration: str = "career", previous_assignments: Mapping[str, str | None] | None = None, fifth_distinct_triggered_obligation: str | None = None) -> dict[str, Any]:
    """Deterministically assign qualified compatible methods without adding calls."""
    validate_catalog(catalog)
    if pipeline not in PIPELINES or purpose not in {"canary", "pilot", "production"}:
        raise AdaptiveV2Error("career assignment scope is invalid")
    if type(career_enabled) is not bool or type(candidate_canary_authorized) is not bool:
        raise AdaptiveV2Error("career layer controls must be boolean")
    _integer(now_epoch, "now_epoch")
    frozen_calls = tuple(sorted(calls, key=lambda item: item.get("reviewer_id", "")))
    if len(frozen_calls) > 7 or len({item.get("reviewer_id") for item in frozen_calls}) != len(frozen_calls):
        raise AdaptiveV2Error("reviewer calls are duplicated or exceed seven")
    for call in frozen_calls:
        _closed(call, {"reviewer_id", "obligations"}, "reviewer call", optional={"primary_obligations"})
        obligations = _strings(call["obligations"], "obligations", nonempty=True)
        primaries = _strings(call.get("primary_obligations", list(obligations)), "primary obligations", nonempty=True)
        if not set(primaries) <= set(obligations) or not set(obligations) <= set(catalog["lens_matrix"]):
            raise AdaptiveV2Error("reviewer obligation contract is invalid")
    frozen_qualifications = tuple(qualifications)
    keys = {(item.get("reviewer_id"), item.get("career_id")) for item in frozen_qualifications}
    if len(keys) != len(frozen_qualifications):
        raise AdaptiveV2Error("duplicate career qualification")
    desired = catalog["desired_distinct_careers"][pipeline]
    primaries_all = {value for call in frozen_calls for value in call.get("primary_obligations", call["obligations"])}
    if pipeline == "S4" and fifth_distinct_triggered_obligation in primaries_all and len(frozen_calls) >= 5 and len(primaries_all) >= 5:
        desired = 5
    career_by_id = {item["career_id"]: item for item in catalog["careers"]}
    candidate_rows: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for call in frozen_calls:
        if not career_enabled:
            candidate_rows.append(((), ("disabled",)))
            continue
        relevant = [item for item in frozen_qualifications if item.get("reviewer_id") == call["reviewer_id"]]
        if not relevant:
            candidate_rows.append(((), ("no_qualified_pairing",)))
            continue
        eligible: set[str] = set()
        refused: set[str] = set()
        obligations = tuple(call["obligations"])
        primaries = tuple(call.get("primary_obligations", obligations))
        for item in relevant:
            required = {"reviewer_id", "career_id", "covered_obligations", "state"}
            _closed(item, required, "qualification", optional={"career_kernel_version", "model_route", "workload_class", "layer_configuration", "expires_at_epoch", "enabled"})
            career_id, state = item["career_id"], item["state"]
            if career_id not in career_by_id or state not in CAREER_STATES:
                raise AdaptiveV2Error("career qualification is invalid")
            compatible = any(career_id in catalog["compatibility"].get(obligation, []) for obligation in primaries)
            complete = set(obligations) <= set(item["covered_obligations"])
            key_matches = item.get("career_kernel_version", "1.0") == "1.0" and item.get("model_route", "default") == model_route and item.get("workload_class", "default") == workload_class and item.get("layer_configuration", "career") == layer_configuration
            if not compatible or not complete or not key_matches:
                refused.add("incompatible_contract")
                continue
            expiry = item.get("expires_at_epoch")
            if expiry is not None and (type(expiry) is not int or expiry <= now_epoch):
                refused.add("expired")
                continue
            state_allowed = purpose in CAREER_STATES[state]
            if state == "candidate" and not candidate_canary_authorized:
                state_allowed = False
            if item.get("enabled", True) is not True or not state_allowed:
                refused.add("disabled")
                continue
            eligible.add(career_id)
        candidate_rows.append((tuple(sorted(eligible)), _ordered_reasons(refused or {"no_qualified_pairing"}) if not eligible else ()))

    @lru_cache(maxsize=None)
    def visit(index: int, selected_careers: frozenset[str], selected_methods: frozenset[str]):
        if index == len(frozen_calls):
            distinct = min(len(selected_methods), desired)
            return (0, distinct, distinct * (distinct - 1) // 2), ()
        options: list[str | None] = [None]
        for career_id in candidate_rows[index][0]:
            method = career_by_id[career_id]["primary_method"]
            if career_id in selected_careers or method not in selected_methods and len(selected_methods) < desired:
                options.append(career_id)
        best = None
        for career_id in options:
            next_careers = selected_careers if career_id is None else selected_careers | {career_id}
            next_methods = selected_methods if career_id is None else selected_methods | {career_by_id[career_id]["primary_method"]}
            score, suffix = visit(index + 1, next_careers, next_methods)
            score = (score[0] + int(career_id is not None), score[1], score[2])
            assignment = (career_id,) + suffix
            tie = tuple(value if value is not None else "~neutral" for value in assignment)
            if best is None or score > best[0] or score == best[0] and tie < tuple(value if value is not None else "~neutral" for value in best[1]):
                best = (score, assignment)
        return best

    selected = visit(0, frozenset(), frozenset())[1]
    previous = dict(previous_assignments or {})
    if set(previous) - {call["reviewer_id"] for call in frozen_calls}:
        raise AdaptiveV2Error("previous assignment references an unknown call")
    bindings, reductions, seen = [], set(), set()
    for call, career_id, row in zip(frozen_calls, selected, candidate_rows):
        if career_id is None:
            reasons = row[1] or (("method_overlap",) if row[0] else ("no_qualified_pairing",))
            reductions.update(reasons)
            bindings.append({"reviewer_id": call["reviewer_id"], "career_id": None, "primary_method": None, "action": "omit", "reused": False, "reasons": list(_ordered_reasons(reasons))})
            continue
        reused = career_id in seen
        prior = previous.get(call["reviewer_id"])
        action = "retain" if prior == career_id or reused else "replace" if prior else "add"
        bindings.append({"reviewer_id": call["reviewer_id"], "career_id": career_id, "primary_method": career_by_id[career_id]["primary_method"], "action": action, "reused": reused, "reasons": []})
        seen.add(career_id)
    actual = len({item for item in selected if item is not None})
    if len(frozen_calls) < desired:
        reductions.add("insufficient_calls")
    if frozen_calls and actual == 0:
        reductions.add("no_qualified_pairing")
    return {"pipeline": pipeline, "desired_distinct_count": desired, "actual_distinct_count": actual, "bindings": bindings, "reduction_reasons": list(_ordered_reasons(reductions)), "model_calls_added": 0}


__all__ = [
    "AdaptiveV2Error", "assign_careers", "decide_admission", "load_json",
    "method_distance", "packets_conflict", "plan_review_stage", "policy_sha256",
    "validate_catalog", "validate_policy", "validate_review_template",
]
