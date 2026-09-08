"""Deterministic selection of the least expensive quality-preserving coding lane."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomy_intent import detect_autonomous_local_intent


LANES = ("T0_DETERMINISTIC", "T1_NATIVE", "T2_OMP_LITE", "T3_PRIME_OMP", "T4_AUTONOMOUS")
_COMPLEX = re.compile(r"\b(architect|migration|redesign|refactor|root cause|debug|investigate|cross[- ]cutting|security|auth|database|schema)\b", re.I)
_EXACT = re.compile(r"\b(exact replace|rename|format|sort|generated|mechanical|repetitive|bump version|update typo)\b", re.I)
_PONYTAIL = re.compile(r"\bponytail\b", re.I)


class ExecutionValuePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionDecision:
    recommended_lane: str
    actual_lane: str
    reason: str
    score: Mapping[str, Any]
    policy_fingerprint: str
    mode: str
    parallel_safe: bool
    maximum_escalations: int

    def public(self) -> dict[str, Any]:
        return {
            "recommended_lane": self.recommended_lane,
            "actual_lane": self.actual_lane,
            "policy_override": self.actual_lane != self.recommended_lane,
            "actual_lane_reason": (
                self.reason if self.actual_lane == self.recommended_lane else
                "SHADOW_MODE_RETAINS_PRIME" if self.mode == "shadow" else
                "RECOMMENDED_LANE_NOT_PROMOTED"
            ),
            "reason": self.reason,
            "score": dict(self.score),
            "policy_fingerprint": self.policy_fingerprint,
            "mode": self.mode,
            "parallel_safe": self.parallel_safe,
            "maximum_escalations": self.maximum_escalations,
        }


class ExecutionValuePolicy:
    def __init__(self, value: Mapping[str, Any]):
        if value.get("schema_version") != "1.0" or value.get("mode") not in {"shadow", "gated", "enforce"}:
            raise ExecutionValuePolicyError("unsupported execution-value policy")
        promoted = value.get("promoted_lanes", [])
        if not isinstance(promoted, list) or any(lane not in LANES[:3] for lane in promoted):
            raise ExecutionValuePolicyError("promoted_lanes are invalid")
        if type(value.get("maximum_escalations")) is not int or value["maximum_escalations"] not in {0, 1}:
            raise ExecutionValuePolicyError("maximum_escalations must be zero or one")
        omp = value.get("omp_lite")
        if not isinstance(omp, Mapping) or type(omp.get("maximum_files")) is not int or not 1 <= omp["maximum_files"] <= 3:
            raise ExecutionValuePolicyError("OMP-lite limits are invalid")
        if omp.get("allowed_risk_tiers") != ["low"] or type(omp.get("require_semantic_assertion")) is not bool:
            raise ExecutionValuePolicyError("OMP-lite safety gates are invalid")
        self.value = dict(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.fingerprint = hashlib.sha256(encoded).hexdigest()

    @classmethod
    def load(cls, path: Path) -> "ExecutionValuePolicy":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def decide(
        self, *, task: str, declared_paths: Sequence[str], risk_tier: str,
        semantic_assertions: Sequence[str] = (), unattended: bool = False,
    ) -> ExecutionDecision:
        if not isinstance(task, str) or not task.strip() or not declared_paths:
            raise ExecutionValuePolicyError("task and declared paths are required")
        if risk_tier not in {"low", "moderate", "high"}:
            raise ExecutionValuePolicyError("risk_tier must be low, moderate, or high")
        if type(unattended) is not bool:
            raise ExecutionValuePolicyError("unattended must be a boolean")
        if isinstance(declared_paths, str) or any(
            not isinstance(path, str) or not path.strip() for path in declared_paths
        ):
            raise ExecutionValuePolicyError("declared paths must be non-empty strings")
        if isinstance(semantic_assertions, str) or any(
            not isinstance(assertion, str) or not assertion.strip() for assertion in semantic_assertions
        ):
            raise ExecutionValuePolicyError("semantic assertions must be non-empty strings")
        paths = tuple(dict.fromkeys(declared_paths))
        path_count = len(paths)
        autonomy = unattended or detect_autonomous_local_intent(task)["activated"]
        ponytail = bool(_PONYTAIL.search(task))
        complex_work = bool(_COMPLEX.search(task))
        exact = bool(_EXACT.search(task))
        low_risk = risk_tier == "low"
        assertions_present = bool(semantic_assertions)
        score = {
            "path_count": path_count, "risk_tier": risk_tier, "autonomous": autonomy,
            "ponytail": ponytail, "complex_signal": complex_work, "exact_signal": exact,
            "semantic_assertions": len(semantic_assertions),
            "omp_lite_maximum_files": self.value["omp_lite"]["maximum_files"],
        }
        if autonomy:
            lane, reason = "T4_AUTONOMOUS", "AUTONOMOUS_INTENT_REQUIRES_DURABLE_LOOP"
        elif ponytail or risk_tier in {"moderate", "high"} or complex_work:
            lane, reason = "T3_PRIME_OMP", "PRIME_REASONING_OR_REVIEW_REQUIRED"
        elif exact and path_count == 1 and low_risk and assertions_present:
            lane, reason = "T0_DETERMINISTIC", "EXACT_SINGLE_PATH_WITH_ASSERTION"
        elif path_count == 1 and low_risk and assertions_present:
            lane, reason = "T1_NATIVE", "SMALL_LOCAL_CHANGE_WITH_ASSERTION"
        elif (
            low_risk and path_count <= self.value["omp_lite"]["maximum_files"]
            and (assertions_present or not self.value["omp_lite"]["require_semantic_assertion"])
        ):
            lane, reason = "T2_OMP_LITE", "BOUNDED_LOW_RISK_OMP_WITH_HOST_VERIFICATION"
        else:
            lane, reason = "T3_PRIME_OMP", "INSUFFICIENT_EVIDENCE_FOR_LOWER_LANE"
        mode = self.value["mode"]
        promoted = set(self.value.get("promoted_lanes", ()))
        actual = lane if mode == "enforce" or lane in {"T3_PRIME_OMP", "T4_AUTONOMOUS"} or (
            mode == "gated" and lane in promoted
        ) else "T3_PRIME_OMP"
        return ExecutionDecision(
            recommended_lane=lane, actual_lane=actual, reason=reason, score=score,
            policy_fingerprint=self.fingerprint, mode=self.value["mode"],
            parallel_safe=actual in {"T0_DETERMINISTIC", "T1_NATIVE"} and path_count == 1,
            maximum_escalations=self.value["maximum_escalations"],
        )
