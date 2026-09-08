"""Deterministic selection of the least expensive quality-preserving coding lane."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

from .autonomy_intent import detect_autonomous_local_intent


LANES = ("T0_DETERMINISTIC", "T1_NATIVE", "T2_OMP_LITE", "T3_PRIME_OMP", "T4_AUTONOMOUS")
_COMPLEX = re.compile(r"\b(architect(?:ure)?|migration|redesign|refactor|root cause|debug|investigate|cross[- ]cutting|security|auth(?:entication|orization)?|credentials?|secrets?|permissions?|deploy(?:ment)?|database|schema)\b", re.I)
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
        if not isinstance(omp, Mapping) or type(omp.get("maximum_files")) is not int or not 1 <= omp["maximum_files"] <= 2:
            raise ExecutionValuePolicyError("OMP-lite limits are invalid")
        if omp.get("allowed_risk_tiers") != ["low"] or type(omp.get("require_semantic_assertion")) is not bool:
            raise ExecutionValuePolicyError("OMP-lite safety gates are invalid")
        if type(omp.get("require_verification", False)) is not bool:
            raise ExecutionValuePolicyError("OMP-lite verification gate must be boolean")
        self.value = dict(value)
        encoded = json.dumps(self.value, sort_keys=True, separators=(",", ":")).encode()
        self.fingerprint = hashlib.sha256(encoded).hexdigest()

    @classmethod
    def load(cls, path: Path) -> "ExecutionValuePolicy":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def decide(
        self, *, task: str, declared_paths: Sequence[str], risk_tier: str,
        semantic_assertions: Sequence[str] = (), unattended: bool = False,
        verification_command_id: str | None = None,
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
        if verification_command_id is not None and verification_command_id not in {"pytest", "bun_test", "packet_static", "python_compile"}:
            raise ExecutionValuePolicyError("verification command is unsupported")
        paths = tuple(dict.fromkeys(declared_paths))
        path_count = len(paths)
        unsafe_path = any(
            PurePosixPath(path.replace("\\", "/")).is_absolute() or PureWindowsPath(path).drive
            or ".." in PurePosixPath(path.replace("\\", "/")).parts
            or path.endswith(("/", "\\"))
            or PurePosixPath(path.replace("\\", "/")).name in {".env", "credentials.json", "secrets.json"}
            for path in paths
        )
        autonomy = unattended or detect_autonomous_local_intent(task)["activated"]
        ponytail = bool(_PONYTAIL.search(task))
        complex_work = bool(_COMPLEX.search(task))
        exact = bool(_EXACT.search(task))
        low_risk = risk_tier == "low"
        assertions_present = bool(semantic_assertions)
        meaningful_verification = assertions_present or verification_command_id in {"pytest", "bun_test"}
        score = {
            "path_count": path_count, "risk_tier": risk_tier, "autonomous": autonomy,
            "ponytail": ponytail, "complex_signal": complex_work, "exact_signal": exact,
            "semantic_assertions": len(semantic_assertions),
            "meaningful_verification": meaningful_verification,
            "omp_lite_maximum_files": self.value["omp_lite"]["maximum_files"],
        }
        if autonomy:
            lane, reason = "T4_AUTONOMOUS", "AUTONOMOUS_INTENT_REQUIRES_DURABLE_LOOP"
        elif unsafe_path or ponytail or risk_tier in {"moderate", "high"} or complex_work:
            lane, reason = "T3_PRIME_OMP", "PRIME_REASONING_OR_REVIEW_REQUIRED"
        elif exact and path_count == 1 and low_risk and assertions_present:
            lane, reason = "T0_DETERMINISTIC", "EXACT_SINGLE_PATH_WITH_ASSERTION"
        elif path_count == 1 and low_risk and assertions_present:
            lane, reason = "T1_NATIVE", "SMALL_LOCAL_CHANGE_WITH_ASSERTION"
        elif (
            low_risk and path_count <= self.value["omp_lite"]["maximum_files"]
            and (meaningful_verification or not self.value["omp_lite"].get("require_verification", False))
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


def t2_recovery_allowed(result: Mapping[str, Any]) -> bool:
    """Only host verification failures with no retained patch may escalate."""
    if result.get("outcome") == "complete" or result.get("execution_lane") != "T2_OMP_LITE":
        return False
    if result.get("reason") not in {"HOST_PATCH_VERIFICATION_FAILED", "SEMANTIC_ASSERTION_FAILED"}:
        return False
    host = result.get("host_apply") or {}
    if host.get("status") != "VERIFIED_FAILED" or host.get("applied") != []:
        return False
    stop = result.get("stop_decision") or {}
    if isinstance(stop, dict) and stop.get("decision") == "ENFORCED_STOP":
        return False
    return (host.get("reason") == "SEMANTIC_ASSERTION_FAILED"
            or bool(host.get("rolled_back")))


def t2_remaining_budget(result: Mapping[str, Any], limits: Mapping[str, int],
                        max_total_tokens: int | None, elapsed_seconds: float) -> dict[str, Any]:
    """No fresh budget on escalation; unknown consumption refuses the retry."""
    import math
    if result.get("provider_call_accounting_complete") is not True or result.get("usage_complete") is not True:
        raise ExecutionValuePolicyError("T2_ESCALATION_ACCOUNTING_INCOMPLETE")
    usage = result.get("usage") or {}
    consumed = {
        "max_provider_calls": result.get("provider_calls"),
        "max_tool_calls": result.get("tool_calls"),
        "max_output_tokens": usage.get("output_tokens"),
        "max_wall_seconds": math.ceil(elapsed_seconds),
    }
    if any(type(n) is not int or n < 0 for n in consumed.values()):
        raise ExecutionValuePolicyError("T2_ESCALATION_ACCOUNTING_INCOMPLETE")
    remaining = dict(limits)
    for key, amount in consumed.items():
        remaining[key] -= amount
    remaining["max_omp_repair_turns"] = 0
    if any(remaining[key] < (2 if key == "max_provider_calls" else 1) for key in consumed):
        raise ExecutionValuePolicyError("T2_ESCALATION_BUDGET_EXHAUSTED")
    total = usage.get("total_tokens")
    if type(total) is not int or total < 0:
        raise ExecutionValuePolicyError("T2_ESCALATION_ACCOUNTING_INCOMPLETE")
    tokens = None if max_total_tokens is None else max_total_tokens - total
    if tokens is not None and tokens < 1:
        raise ExecutionValuePolicyError("T2_ESCALATION_BUDGET_EXHAUSTED")
    return {"limits": remaining, "max_total_tokens": tokens}


def evaluate_t2_trials(pairs: Sequence[Mapping[str, Any]], promotion: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate paired live receipts without a model or invented missing usage."""
    import statistics
    errors = []
    if len(pairs) < promotion["minimum_trials"]:
        errors.append("INSUFFICIENT_PAIRED_TRIALS")
    ids = [pair.get("trial_id") for pair in pairs]
    if len(set(ids)) != len(ids) or any(not value for value in ids):
        errors.append("TRIAL_IDS_INVALID")
    latencies, tokens = {"T2": [], "T3": []}, {"T2": [], "T3": []}
    for pair in pairs:
        if pair.get("trial_class") != "live_matched" or pair.get("baseline_failed") is not True:
            errors.append("LIVE_BEHAVIORAL_BASELINE_REQUIRED")
        for lane in ("T2", "T3"):
            receipt = pair.get(lane, {})
            host = receipt.get("host_apply") or {}
            test = host.get("test") or {}
            if (receipt.get("outcome") != "complete" or receipt.get("integration_authorized") is not True
                    or host.get("status") != "VERIFIED_OK" or test.get("exit_code") != 0
                    or test.get("command_id") not in {"pytest", "bun_test"}
                    or receipt.get("oracle_passed") is not True
                    or receipt.get("scope_preserved") is not True
                    or receipt.get("execution_lane") != {"T2": "T2_OMP_LITE", "T3": "T3_PRIME_OMP"}[lane]
                    or receipt.get("prime_active") is not (lane == "T3")):
                errors.append("QUALITY_OR_SCOPE_FAILED")
            usage = receipt.get("usage") or {}
            wall, total = receipt.get("trial_wall_time_ms"), usage.get("total_tokens")
            if (receipt.get("usage_complete") is not True
                    or receipt.get("provider_call_accounting_complete") is not True
                    or type(wall) is not int or wall <= 0 or type(total) is not int or total <= 0):
                errors.append("MEASUREMENT_INCOMPLETE")
            else:
                latencies[lane].append(wall)
                tokens[lane].append(total)
    metrics = {}
    if pairs and all(len(latencies[lane]) == len(pairs) for lane in ("T2", "T3")):
        metrics = {
            "latency_reduction": 1 - statistics.median(latencies["T2"]) / statistics.median(latencies["T3"]),
            "provider_usage_reduction": 1 - sum(tokens["T2"]) / sum(tokens["T3"]),
        }
        if metrics["latency_reduction"] < promotion["minimum_latency_reduction"]:
            errors.append("LATENCY_THRESHOLD_NOT_MET")
        if metrics["provider_usage_reduction"] < promotion["minimum_provider_usage_reduction"]:
            errors.append("USAGE_THRESHOLD_NOT_MET")
    return {"schema_version": "t2-qualification.v1", "status": "PASS" if not errors else "FAIL",
            "activation": not errors, "trial_count": len(pairs), "model_calls": 0,
            "reasons": sorted(set(errors)), "metrics": metrics}
