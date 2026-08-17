"""Closed staged-rollout and production-admission policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RolloutPolicyError(RuntimeError):
    pass


PROFILE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
REQUIRED_CHECKS = frozenset({
    "rpc-contract", "model-route", "containment", "capability-boundary",
    "profile-matrix", "native-hermes",
})


@dataclass(frozen=True)
class AdmissionLimits:
    max_active_global: int
    max_active_per_profile: int
    max_cpu_percent: int
    max_memory_percent: int
    sample_max_age_seconds: int
    native_reserve_percent: int
    unknown_resource_action: str


@dataclass(frozen=True)
class RolloutPolicy:
    enabled: bool
    stage: str
    canary_profiles: tuple[str, ...]
    excluded_profiles: tuple[str, ...]
    minimum_eligible_coverage_percent: int
    admission: AdmissionLimits
    required_update_checks: frozenset[str]
    compatibility_evidence_max_age_seconds: int
    activation: str
    explicit_handoff_required: bool
    native_fallback: bool

    @classmethod
    def load(cls, path: Path) -> RolloutPolicy:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RolloutPolicyError("rollout policy is unavailable") from exc
        expected = {
            "schema_version", "enabled", "stage", "canary_profiles",
            "excluded_profiles", "minimum_eligible_coverage_percent", "admission",
            "required_update_checks", "compatibility_evidence_max_age_seconds",
            "activation", "explicit_handoff_required", "native_fallback",
        }
        if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != "1.0":
            raise RolloutPolicyError("rollout policy keys are not closed")
        if type(value["enabled"]) is not bool or value["stage"] not in {"disabled", "canary", "broad"}:
            raise RolloutPolicyError("invalid rollout state")
        canary = cls._profiles(value["canary_profiles"], "canary")
        excluded = cls._profiles(value["excluded_profiles"], "excluded")
        if not canary or set(canary) & set(excluded):
            raise RolloutPolicyError("rollout profile cohorts are invalid")
        coverage = value["minimum_eligible_coverage_percent"]
        if type(coverage) is not int or not 50 <= coverage <= 100:
            raise RolloutPolicyError("unsafe rollout coverage threshold")
        admission = value["admission"]
        admission_keys = {
            "max_active_global", "max_active_per_profile", "max_cpu_percent",
            "max_memory_percent", "sample_max_age_seconds", "native_reserve_percent",
            "unknown_resource_action",
        }
        if not isinstance(admission, dict) or set(admission) != admission_keys:
            raise RolloutPolicyError("admission policy keys are not closed")
        integer_keys = admission_keys - {"unknown_resource_action"}
        if any(type(admission[key]) is not int for key in integer_keys):
            raise RolloutPolicyError("admission limits must be integers")
        if not 1 <= admission["max_active_per_profile"] <= admission["max_active_global"] <= 16:
            raise RolloutPolicyError("unsafe active-job limits")
        if not 1 <= admission["max_cpu_percent"] <= 90 or not 1 <= admission["max_memory_percent"] <= 90:
            raise RolloutPolicyError("unsafe resource thresholds")
        if not 1 <= admission["sample_max_age_seconds"] <= 300 or not 10 <= admission["native_reserve_percent"] <= 90:
            raise RolloutPolicyError("unsafe freshness or native reserve")
        if admission["unknown_resource_action"] != "reject-prime":
            raise RolloutPolicyError("unknown resource state must reject Prime")
        checks = value["required_update_checks"]
        if not isinstance(checks, list) or len(checks) != len(set(checks)) or set(checks) != REQUIRED_CHECKS:
            raise RolloutPolicyError("required compatibility checks cannot be weakened")
        age = value["compatibility_evidence_max_age_seconds"]
        if type(age) is not int or not 60 <= age <= 3600:
            raise RolloutPolicyError("invalid compatibility evidence age")
        if value["activation"] != "restart-boundary" or value["explicit_handoff_required"] is not True or value["native_fallback"] is not True:
            raise RolloutPolicyError("native and activation safeguards are mandatory")
        return cls(
            enabled=value["enabled"], stage=value["stage"], canary_profiles=canary,
            excluded_profiles=excluded, minimum_eligible_coverage_percent=coverage,
            admission=AdmissionLimits(**admission), required_update_checks=frozenset(checks),
            compatibility_evidence_max_age_seconds=age, activation="restart-boundary",
            explicit_handoff_required=True, native_fallback=True,
        )

    @staticmethod
    def _profiles(value: Any, label: str) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) != len(set(value)) or any(
            not isinstance(item, str) or not PROFILE.fullmatch(item) for item in value
        ):
            raise RolloutPolicyError(f"invalid {label} profiles")
        return tuple(value)

    def profile_eligible(self, profile: str) -> bool:
        if not PROFILE.fullmatch(profile) or profile in self.excluded_profiles:
            return False
        if not self.enabled or self.stage == "disabled":
            return False
        return self.stage == "broad" or profile in self.canary_profiles
