"""Evidence-gated execution-depth selection that never removes safety gates."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from prime_bridge.verified_success import is_verified_success


class AdaptiveDepthError(RuntimeError):
    pass


PROFILES = ("compact", "balanced", "full")
NUMERIC_KEYS = ("max_files", "max_changed_bytes", "max_complexity", "max_tool_calls", "max_repair_turns", "max_review_bytes")
SAFETY_KEYS = ("require_prime", "require_omp", "require_containment", "require_patch_guard", "require_host_apply", "require_host_verification")
PROFILE_KEYS = frozenset(NUMERIC_KEYS + SAFETY_KEYS)
HARNESSES = frozenset({"hermes", "codex", "claude-code", "moa"})


class VerifiedDepthEvidence(dict[str, Any]):
    """Aggregate created only after source receipts pass independent checks."""


def verify_depth_evidence(value: Mapping[str, Any], receipt_root: Path) -> VerifiedDepthEvidence:
    required = {"schema_version", "captured_at", "policy_revision", "route_fingerprint",
                "window_start", "window_end", "source_receipts"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema_version") != "1.0":
        raise AdaptiveDepthError("depth evidence manifest is invalid")
    sources = value.get("source_receipts")
    if not isinstance(sources, list) or not sources:
        raise AdaptiveDepthError("depth evidence has no source receipts")
    try:
        window_start = datetime.fromisoformat(str(value["window_start"]).replace("Z", "+00:00"))
        window_end = datetime.fromisoformat(str(value["window_end"]).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AdaptiveDepthError("depth evidence window is invalid") from exc
    if window_start >= window_end:
        raise AdaptiveDepthError("depth evidence window is invalid")
    declared = {}
    for source in sources:
        if not isinstance(source, Mapping) or set(source) != {"execution_id", "sha256"}:
            raise AdaptiveDepthError("depth evidence source is invalid")
        execution_id, expected = source["execution_id"], source["sha256"]
        if not isinstance(execution_id, str) or execution_id in declared or not isinstance(expected, str) or len(expected) != 64:
            raise AdaptiveDepthError("depth evidence source identity is invalid")
        declared[execution_id] = expected
    counts = {name: {"attempts": 0, "successes": 0} for name in HARNESSES}
    observed: dict[str, str] = {}
    for path in Path(receipt_root).glob("*.json"):
        try:
            payload = path.read_bytes()
            receipt = json.loads(payload)
            started = datetime.fromisoformat(str(receipt.get("started_at", "")).replace("Z", "+00:00"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not (window_start <= started < window_end) or receipt.get("model_route_fingerprint") != value["route_fingerprint"]:
            continue
        execution_id = receipt.get("execution_id")
        harness = receipt.get("harness")
        if not isinstance(execution_id, str) or path.name != f"{execution_id}.json" or harness not in HARNESSES:
            raise AdaptiveDepthError("eligible depth receipt identity is invalid")
        observed[execution_id] = hashlib.sha256(payload).hexdigest()
        counts[harness]["attempts"] += 1
        counts[harness]["successes"] += int(is_verified_success(receipt))
    if observed != declared:
        raise AdaptiveDepthError("depth evidence manifest omits or changes eligible receipts")
    aggregate = {}
    for harness, values in counts.items():
        count, successes = values["attempts"], values["successes"]
        z = 1.96
        denominator = 1 + z * z / count if count else 1
        rate = successes / count if count else 0
        center = rate + z * z / (2 * count) if count else 0
        margin = z * math.sqrt((rate * (1 - rate) + z * z / (4 * count)) / count) if count else 0
        aggregate[harness] = {"count": count, "wilson_lower_bound": (center - margin) / denominator if count else 0.0}
    return VerifiedDepthEvidence({
        "schema_version": "1.0", "captured_at": value["captured_at"],
        "policy_revision": value["policy_revision"], "route_fingerprint": value["route_fingerprint"],
        "harness": aggregate,
    })


class AdaptiveDepthPolicy:
    def __init__(self, *, enabled: bool, minimum_samples: int, minimum_wilson_lower_bound: float,
                 max_evidence_age_days: int, profiles: Mapping[str, Mapping[str, Any]]):
        if not isinstance(enabled, bool) or set(profiles) != set(PROFILES):
            raise AdaptiveDepthError("depth profiles are not closed")
        if type(minimum_samples) is not int or minimum_samples <= 0:
            raise AdaptiveDepthError("minimum_samples must be positive")
        if type(minimum_wilson_lower_bound) not in {int, float} or not 0 < minimum_wilson_lower_bound <= 1:
            raise AdaptiveDepthError("minimum Wilson bound is invalid")
        if type(max_evidence_age_days) is not int or max_evidence_age_days <= 0:
            raise AdaptiveDepthError("evidence age must be positive")
        normalized: dict[str, dict[str, Any]] = {}
        prior = {key: -1 for key in NUMERIC_KEYS}
        for name in PROFILES:
            profile = profiles[name]
            if not isinstance(profile, Mapping) or set(profile) != PROFILE_KEYS:
                raise AdaptiveDepthError(f"{name} profile keys are not closed")
            item = dict(profile)
            for key in NUMERIC_KEYS:
                if type(item[key]) is not int or item[key] <= 0 or item[key] < prior[key]:
                    raise AdaptiveDepthError(f"{name}.{key} is invalid or non-monotonic")
                prior[key] = item[key]
            if any(item[key] is not True for key in SAFETY_KEYS):
                raise AdaptiveDepthError("every depth must retain all mandatory gates")
            normalized[name] = item
        self.enabled = enabled
        self.minimum_samples = minimum_samples
        self.minimum_wilson_lower_bound = float(minimum_wilson_lower_bound)
        self.max_evidence_age_days = max_evidence_age_days
        self.profiles = normalized
        self.policy_revision = hashlib.sha256(json.dumps({
            "minimum_samples": minimum_samples, "minimum_wilson_lower_bound": minimum_wilson_lower_bound,
            "max_evidence_age_days": max_evidence_age_days, "profiles": normalized,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def load(cls, path: str | Path) -> "AdaptiveDepthPolicy":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdaptiveDepthError("depth policy is unreadable") from exc
        expected = {"schema_version", "enabled", "minimum_samples", "minimum_wilson_lower_bound", "max_evidence_age_days", "profiles"}
        if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != "1.0":
            raise AdaptiveDepthError("depth policy is not a supported closed schema")
        return cls(enabled=value["enabled"], minimum_samples=value["minimum_samples"],
                   minimum_wilson_lower_bound=value["minimum_wilson_lower_bound"],
                   max_evidence_age_days=value["max_evidence_age_days"], profiles=value["profiles"])

    def limits_for(self, profile: str) -> dict[str, Any]:
        if profile not in self.profiles:
            raise AdaptiveDepthError("unknown depth profile")
        return dict(self.profiles[profile])

    def select(self, *, task_tier: str, harness: str, route_fingerprint: str,
               evidence: Mapping[str, Any] | None = None, current_execution_id: str | None = None,
               now: datetime | None = None) -> dict[str, Any]:
        def full(reason: str) -> dict[str, Any]:
            return {"profile": "full", "reason": reason, "evidence_fingerprint": None, "limits": self.limits_for("full")}

        if not self.enabled:
            return full("POLICY_DISABLED")
        if task_tier in {"high_risk", "complex"}:
            return full("TIER_REQUIRES_FULL")
        if harness not in HARNESSES or not isinstance(evidence, Mapping):
            return full("EVIDENCE_UNAVAILABLE")
        if not isinstance(evidence, VerifiedDepthEvidence):
            return full("EVIDENCE_NOT_INDEPENDENTLY_VERIFIED")
        required = {"schema_version", "captured_at", "policy_revision", "route_fingerprint", "harness"}
        if set(evidence) != required or evidence.get("schema_version") != "1.0":
            return full("EVIDENCE_INVALID")
        if evidence.get("route_fingerprint") != route_fingerprint:
            return full("ROUTE_MISMATCH")
        if evidence.get("policy_revision") != self.policy_revision:
            return full("POLICY_REVISION_MISMATCH")
        if current_execution_id and evidence.get("policy_revision") == current_execution_id:
            return full("SELF_GRADING_EVIDENCE")
        try:
            captured = datetime.fromisoformat(str(evidence["captured_at"]).replace("Z", "+00:00"))
            current = now or datetime.now(timezone.utc)
            age_days = (current - captured).total_seconds() / 86400
        except (TypeError, ValueError):
            return full("EVIDENCE_INVALID")
        if age_days < 0 or age_days > self.max_evidence_age_days:
            return full("EVIDENCE_STALE")
        by_harness = evidence.get("harness")
        if not isinstance(by_harness, Mapping) or set(by_harness) != HARNESSES:
            return full("HARNESS_COVERAGE_INCOMPLETE")
        for name in sorted(HARNESSES):
            result = by_harness.get(name)
            if not isinstance(result, Mapping) or type(result.get("count")) is not int or result["count"] < self.minimum_samples:
                return full("INSUFFICIENT_SAMPLES")
            bound = result.get("wilson_lower_bound")
            if type(bound) not in {int, float} or bound < self.minimum_wilson_lower_bound:
                return full("LOW_CONFIDENCE")
        profile = "compact" if task_tier == "atomic" else "balanced"
        fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"profile": profile, "reason": "VERIFIED_EVIDENCE", "evidence_fingerprint": fingerprint, "limits": self.limits_for(profile)}
