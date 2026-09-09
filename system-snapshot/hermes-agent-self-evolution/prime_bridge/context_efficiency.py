"""Deterministic context projection and rendering-profile isolation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class ContextEfficiencyError(RuntimeError):
    pass


PROFILES = frozenset({"governed", "concise_user", "narrative_user", "verbatim"})
TIERS = frozenset({"atomic", "standard", "complex", "high_risk"})
SECTION_KEYS = frozenset({"category", "content", "required", "priority", "source_type"})
SUMMARY_KEYS = frozenset({"summary", "raw_sha256", "raw_bytes", "binding_sha256", "binding_bytes"})
GOVERNED_TEMPLATES = {
    "BUDGET_EXHAUSTED": "The request exceeds the approved execution budget.",
    "SUMMARY_PROVENANCE_INVALID": "The summary does not match its source binding.",
    "RENDERING_PROFILE_DENIED": "The rendering profile cannot process this surface.",
}

PHASE32_REQUIRED_CHECKS = {
    "research": frozenset({"all_required_claims_mapped", "no_unknown_source_ids", "unsupported_claim_count_zero"}),
    "review": frozenset({"seeded_issue_recall", "false_positive_rate", "evidence_anchor_valid"}),
    "recovery": frozenset({"effect_identity_exact", "repeated_effects_zero", "verified_terminal_state"}),
}
PHASE32_EXPECTED_ARTIFACT = {
    "research": ("report.md", "claim-source-matrix.json"),
    "review": ("review.json",),
    "recovery": ("recovery-receipt.json",),
}

SCHEMA_BOUND_OUTPUT_KEYS = {
    "research": frozenset({"claims"}),
    "review": frozenset({"findings"}),
    "recovery": frozenset({"effect_id", "effect_count", "verified_completion"}),
}
SCHEMA_BOUND_ECHO_KEYS = {
    "research": frozenset({"task", "sources", "input"}),
    "review": frozenset({"task", "review_target", "input"}),
    "recovery": frozenset({"task", "scenario", "input"}),
}
HOST_TERMINAL_RECEIPT_KEYS = frozenset({
    "schema_version", "session_id", "task_id", "effect_id", "effect_count",
    "terminal_state", "final_event_digest", "source_sha256",
})


@dataclass(frozen=True)
class TaskRelevantProjection:
    category: str
    canonical_payload: Mapping[str, Any]
    payload_sha256: str
    tool_schema_mode: str
    selected_tool_schemas: tuple[str, ...]
    terminal_early_stop: bool
    canonical_evidence_retained: bool
    model_calls: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "canonical_payload": dict(self.canonical_payload),
            "payload_sha256": self.payload_sha256,
            "tool_schema_mode": self.tool_schema_mode,
            "selected_tool_schemas": list(self.selected_tool_schemas),
            "terminal_early_stop": self.terminal_early_stop,
            "canonical_evidence_retained": self.canonical_evidence_retained,
            "model_calls": self.model_calls,
        }


def task_relevant_projection(
    *, task_manifest: Mapping[str, Any], task_input: Mapping[str, Any],
    output_schema: Mapping[str, Any], enabled_categories: Sequence[str] = (),
) -> TaskRelevantProjection | None:
    """Select a zero-tool projection from structured task facts only.

    Canonical task and scenario/review evidence are copied in full. The only
    removed model-visible material is the unused tool-schema bundle.
    """
    if not isinstance(task_manifest, Mapping) or not isinstance(task_input, Mapping):
        raise ContextEfficiencyError("task projection inputs must be mappings")
    category = task_manifest.get("stratum")
    if category not in PHASE32_REQUIRED_CHECKS or category not in set(enabled_categories):
        return None
    if task_manifest.get("schema_version") != "1.0":
        raise ContextEfficiencyError("task projection manifest schema is unsupported")
    checks = task_manifest.get("deterministic_checks")
    if not isinstance(checks, list) or not PHASE32_REQUIRED_CHECKS[category].issubset(checks):
        raise ContextEfficiencyError("task projection omits a required deterministic check")
    artifacts = task_manifest.get("expected_artifacts")
    if artifacts != list(PHASE32_EXPECTED_ARTIFACT[category]):
        raise ContextEfficiencyError("task projection artifact contract is invalid")
    if category == "research":
        if (
            not isinstance(task_manifest.get("required_claim_ids"), list)
            or not isinstance(task_manifest.get("allowed_source_ids"), list)
            or set(task_input) != {"sources"}
            or not isinstance(task_input.get("sources"), list)
        ):
            raise ContextEfficiencyError("research projection evidence is incomplete")
        expected_schema = {
            "claims": [{"claim_id": "required claim id", "source_id": "allowed source id"}],
        }
    elif category == "review":
        if not isinstance(task_manifest.get("seeded_issue_ids"), list) or set(task_input) != {"fixture_lines"}:
            raise ContextEfficiencyError("review projection evidence is incomplete")
        expected_schema = {"findings": [{"issue_id": "seeded issue id", "evidence_line": 0}]}
    else:
        required = {"expected_effect_id", "fault"}
        if not required.issubset(task_manifest) or set(task_input) != {"effect_id", "expected_effects", "fault"}:
            raise ContextEfficiencyError("recovery projection evidence is incomplete")
        expected_schema = {"effect_id": "exact expected id", "effect_count": 1, "verified_completion": True}
    if dict(output_schema) != expected_schema:
        raise ContextEfficiencyError("task projection output contract is invalid")
    payload = {
        "task": json.loads(json.dumps(dict(task_manifest), sort_keys=True)),
        "input": json.loads(json.dumps(dict(task_input), sort_keys=True)),
        "output_schema": json.loads(json.dumps(dict(output_schema), sort_keys=True)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return TaskRelevantProjection(
        category=str(category), canonical_payload=payload,
        payload_sha256=hashlib.sha256(encoded).hexdigest(),
        tool_schema_mode="none", selected_tool_schemas=(),
        terminal_early_stop=True, canonical_evidence_retained=True,
    )


def _valid_schema_bound_types(category: str, value: Mapping[str, Any]) -> bool:
    if category == "research":
        rows = value.get("claims")
        return isinstance(rows, list) and all(
            isinstance(row, Mapping)
            and set(row) == {"claim_id", "source_id"}
            and isinstance(row["claim_id"], str)
            and isinstance(row["source_id"], str)
            for row in rows
        )
    if category == "review":
        rows = value.get("findings")
        return isinstance(rows, list) and all(
            isinstance(row, Mapping)
            and set(row) == {"issue_id", "evidence_line"}
            and isinstance(row["issue_id"], str)
            and type(row["evidence_line"]) is int
            for row in rows
        )
    return (
        isinstance(value.get("effect_id"), str)
        and type(value.get("effect_count")) is int
        and type(value.get("verified_completion")) is bool
    )


def normalize_schema_bound_output(
    category: str,
    output: Mapping[str, Any],
    *,
    canonical_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract one closed result shape while treating echoed input as data.

    This function changes structure only. It never supplies a missing result
    field or changes a model-produced result value.
    """
    if category not in SCHEMA_BOUND_OUTPUT_KEYS or not isinstance(output, Mapping):
        raise ContextEfficiencyError("schema-bound output category or payload is invalid")
    value = dict(output)
    expected = SCHEMA_BOUND_OUTPUT_KEYS[category]
    if set(value) == expected:
        result = value
    else:
        nested = value.get("output_schema")
        if not isinstance(nested, Mapping) or set(nested) != expected:
            raise ContextEfficiencyError("schema-bound output does not match its declared result shape")
        echoes = set(value) - {"output_schema"}
        if echoes:
            if not isinstance(canonical_payload, Mapping):
                raise ContextEfficiencyError("schema-bound output echoes lack canonical evidence")
            allowed = SCHEMA_BOUND_ECHO_KEYS[category]
            if not echoes.issubset(allowed) or not echoes.issubset(canonical_payload):
                raise ContextEfficiencyError("schema-bound output contains an unknown echo")
            if any(_stable_json(value[key]) != _stable_json(canonical_payload[key]) for key in echoes):
                raise ContextEfficiencyError("schema-bound output conflicts with canonical evidence")
        result = dict(nested)
    if not _valid_schema_bound_types(category, result):
        raise ContextEfficiencyError("schema-bound output field types are invalid")
    return result


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ContextEfficiencyError("canonical evidence is not JSON-compatible") from exc


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def settle_recovery_terminal_state(
    output: Mapping[str, Any],
    *,
    canonical_payload: Mapping[str, Any],
    terminal_receipt: Mapping[str, Any],
    expected_session_id: str,
    expected_task_id: str,
    expected_effect_id: str,
    expected_source_sha256: str,
) -> dict[str, Any]:
    """Settle recovery truth from a host receipt, never the model boolean."""
    normalized = normalize_schema_bound_output(
        "recovery", output, canonical_payload=canonical_payload,
    )
    if not isinstance(terminal_receipt, Mapping) or set(terminal_receipt) != HOST_TERMINAL_RECEIPT_KEYS:
        raise ContextEfficiencyError("host terminal receipt fields are not closed")
    receipt = dict(terminal_receipt)
    expected = {
        "schema_version": "1.0",
        "session_id": expected_session_id,
        "task_id": expected_task_id,
        "effect_id": expected_effect_id,
        "effect_count": 1,
        "terminal_state": "completed",
        "source_sha256": expected_source_sha256,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ContextEfficiencyError("host terminal receipt does not match the recovery boundary")
    if not _is_sha256(receipt.get("final_event_digest")) or not _is_sha256(receipt.get("source_sha256")):
        raise ContextEfficiencyError("host terminal receipt digest is invalid")
    if (
        normalized["effect_id"] != receipt["effect_id"]
        or normalized["effect_count"] != receipt["effect_count"]
    ):
        raise ContextEfficiencyError("model effect identity conflicts with host terminal evidence")
    return {
        "effect_id": normalized["effect_id"],
        "effect_count": normalized["effect_count"],
        "verified_completion": True,
    }


def normalize_projected_output(
    category: str, output: Mapping[str, Any], *,
    canonical_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper that leaves invalid model output unmodified."""
    if category not in {"review", "recovery"} or not isinstance(output, Mapping):
        raise ContextEfficiencyError("projected output category or payload is invalid")
    try:
        return normalize_schema_bound_output(
            category, output, canonical_payload=canonical_payload,
        )
    except ContextEfficiencyError:
        return dict(output)


class RenderingPolicy:
    def __init__(self, *, profiles: Mapping[str, Sequence[str]], tier_context_bytes: Mapping[str, int],
                 tier_document_limits: Mapping[str, int], allowed_categories: Sequence[str]):
        if set(profiles) != PROFILES or set(tier_context_bytes) != TIERS or set(tier_document_limits) != TIERS:
            raise ContextEfficiencyError("rendering policy keys are not closed")
        normalized_profiles: dict[str, frozenset[str]] = {}
        seen: set[str] = set()
        for profile, surfaces in profiles.items():
            values = tuple(surfaces)
            if not values or len(values) != len(set(values)) or seen & set(values):
                raise ContextEfficiencyError("rendering surfaces overlap or are invalid")
            seen.update(values)
            normalized_profiles[profile] = frozenset(values)
        for limits in (tier_context_bytes, tier_document_limits):
            if any(type(value) is not int or value <= 0 for value in limits.values()):
                raise ContextEfficiencyError("context limits must be positive integers")
        categories = tuple(allowed_categories)
        if not categories or len(categories) != len(set(categories)):
            raise ContextEfficiencyError("context categories are invalid")
        self.profiles = normalized_profiles
        self.tier_context_bytes = dict(tier_context_bytes)
        self.tier_document_limits = dict(tier_document_limits)
        self.allowed_categories = frozenset(categories)

    @classmethod
    def load(cls, path: str | Path) -> "RenderingPolicy":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContextEfficiencyError("rendering policy is unreadable") from exc
        expected = {"schema_version", "profiles", "tier_context_bytes", "tier_document_limits", "allowed_categories"}
        if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != "1.0":
            raise ContextEfficiencyError("rendering policy is not a supported closed schema")
        return cls(profiles=value["profiles"], tier_context_bytes=value["tier_context_bytes"],
                   tier_document_limits=value["tier_document_limits"], allowed_categories=value["allowed_categories"])

    def validate_surface(self, profile: str, surface: str) -> None:
        if profile not in self.profiles or surface not in self.profiles[profile]:
            raise ContextEfficiencyError("rendering profile cannot process this surface")


def make_bounded_summary(raw_output: str, summary: str, *, max_summary_bytes: int) -> dict[str, Any]:
    if not isinstance(raw_output, str) or not isinstance(summary, str) or not summary.strip():
        raise ContextEfficiencyError("summary inputs are invalid")
    encoded = summary.strip().encode()
    if len(encoded) > max_summary_bytes:
        raise ContextEfficiencyError("summary exceeds its byte budget")
    raw_encoded = raw_output.encode()
    digest = hashlib.sha256(raw_encoded).hexdigest()
    return {"summary": summary.strip(), "raw_sha256": digest, "raw_bytes": len(raw_encoded),
            "binding_sha256": digest, "binding_bytes": len(raw_encoded)}


def validate_bounded_summary(value: Mapping[str, Any], *, max_summary_bytes: int,
                             raw_source: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != SUMMARY_KEYS:
        raise ContextEfficiencyError("summary fields are not closed")
    if value["raw_sha256"] != value["binding_sha256"] or value["raw_bytes"] != value["binding_bytes"]:
        raise ContextEfficiencyError("summary provenance binding is invalid")
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise ContextEfficiencyError("summary text is invalid")
    if len(value["summary"].encode()) > max_summary_bytes:
        raise ContextEfficiencyError("summary exceeds its byte budget")
    if not isinstance(value["raw_sha256"], str) or len(value["raw_sha256"]) != 64:
        raise ContextEfficiencyError("summary source hash is invalid")
    if type(value["raw_bytes"]) is not int or value["raw_bytes"] < 0:
        raise ContextEfficiencyError("summary source size is invalid")
    if raw_source is None:
        raise ContextEfficiencyError("summary source evidence is required")
    encoded_source = raw_source.encode()
    if hashlib.sha256(encoded_source).hexdigest() != value["raw_sha256"] or len(encoded_source) != value["raw_bytes"]:
        raise ContextEfficiencyError("summary source evidence does not match its binding")
    if len(value["summary"].encode()) >= len(encoded_source):
        raise ContextEfficiencyError("summary does not reduce source bytes")
    return dict(value)


def project_context(*, policy: RenderingPolicy, task_tier: str, profile: str, surface: str,
                    sections: Sequence[Mapping[str, Any]] = (),
                    bounded_summaries: Sequence[Mapping[str, Any]] = (),
                    summary_sources: Mapping[str, str] | None = None) -> dict[str, Any]:
    policy.validate_surface(profile, surface)
    if task_tier not in TIERS:
        raise ContextEfficiencyError("task tier is invalid")
    maximum_bytes = policy.tier_context_bytes[task_tier]
    maximum_documents = policy.tier_document_limits[task_tier]
    candidates: list[dict[str, Any]] = []
    retrieved_bytes = 0
    summary_bytes = 0
    for index, raw in enumerate(sections):
        if not isinstance(raw, Mapping) or set(raw) != SECTION_KEYS:
            raise ContextEfficiencyError("context section fields are not closed")
        item = dict(raw)
        if item["category"] not in policy.allowed_categories or not isinstance(item["content"], str):
            raise ContextEfficiencyError("context section is invalid")
        if type(item["required"]) is not bool or type(item["priority"]) is not int:
            raise ContextEfficiencyError("context section priority is invalid")
        item["_index"] = index
        item["_digest"] = hashlib.sha256(item["content"].encode()).hexdigest()
        candidates.append(item)
        retrieved_bytes += len(item["content"].encode())
    for raw in bounded_summaries:
        source = (summary_sources or {}).get(str(raw.get("raw_sha256"))) if isinstance(raw, Mapping) else None
        item = validate_bounded_summary(raw, max_summary_bytes=maximum_bytes, raw_source=source)
        summary_bytes += len(item["summary"].encode())
        candidates.append({"category": "tool_output", "content": item["summary"], "required": False,
                           "priority": 0, "source_type": "summary", "_index": len(candidates),
                           "_digest": hashlib.sha256(item["summary"].encode()).hexdigest()})
    candidates.sort(key=lambda item: (not item["required"], -item["priority"], item["_index"]))
    selected: list[dict[str, Any]] = []
    dropped: set[str] = set()
    seen: set[str] = set()
    used = 0
    for item in candidates:
        if item["_digest"] in seen:
            dropped.add(item["category"])
            continue
        size = len(item["content"].encode())
        if len(selected) >= maximum_documents or used + size > maximum_bytes:
            if item["required"]:
                raise ContextEfficiencyError("required context exceeds its budget")
            dropped.add(item["category"])
            continue
        seen.add(item["_digest"])
        used += size
        selected.append({key: item[key] for key in SECTION_KEYS})
    ratio = used / maximum_bytes
    band = "PEAK" if ratio <= .3 else "GOOD" if ratio <= .5 else "DEGRADING" if ratio <= .7 else "POOR"
    projection = {"profile": profile, "surface": surface, "sections": selected}
    packet_bytes = len(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode())
    return {"projection": projection, "packet_bytes": packet_bytes, "retrieved_bytes": retrieved_bytes,
            "summary_bytes": summary_bytes, "dropped_section_categories": sorted(dropped),
            "context_pressure_band": band, "rendering_profile": profile, "model_calls": 0}
