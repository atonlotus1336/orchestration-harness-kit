"""Shadow-first marginal-value decisions with per-rule failure isolation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


class MarginalValueError(RuntimeError):
    pass


RULES = frozenset({"retry", "repair", "context_expansion"})
RULE_KEYS = frozenset({"enabled", "minimum_expected_success_gain"})
PROMOTION_KEYS = frozenset({"decision", "policy_revision", "synthetic", "corpus_sha256", "quality_non_regression"})


class MarginalValuePolicy:
    def __init__(self, *, enabled: bool, mode: str, policy_revision: str,
                 minimum_samples: int, minimum_wilson_lower_bound: float,
                 rollback_enabled: bool, high_risk_excluded: bool,
                 rules: Mapping[str, Mapping[str, Any]]):
        if not isinstance(enabled, bool) or mode not in {"shadow", "enforce"}:
            raise MarginalValueError("stop policy mode is invalid")
        if not isinstance(policy_revision, str) or not policy_revision:
            raise MarginalValueError("policy revision is required")
        if type(minimum_samples) is not int or minimum_samples <= 0:
            raise MarginalValueError("minimum samples must be positive")
        if type(minimum_wilson_lower_bound) not in {int, float} or not 0 < minimum_wilson_lower_bound <= 1:
            raise MarginalValueError("minimum Wilson bound is invalid")
        if rollback_enabled is not True or not isinstance(high_risk_excluded, bool):
            raise MarginalValueError("rollback and high-risk controls are invalid")
        if set(rules) != RULES:
            raise MarginalValueError("stop rules are not closed")
        normalized = {}
        for name, rule in rules.items():
            if not isinstance(rule, Mapping) or set(rule) != RULE_KEYS or not isinstance(rule["enabled"], bool):
                raise MarginalValueError(f"{name} rule is invalid")
            gain = rule["minimum_expected_success_gain"]
            if type(gain) not in {int, float} or not 0 <= gain <= 1:
                raise MarginalValueError(f"{name} expected gain is invalid")
            normalized[name] = {"enabled": rule["enabled"], "minimum_expected_success_gain": float(gain)}
        self.enabled = enabled
        self.mode = mode
        self.policy_revision = policy_revision
        self.minimum_samples = minimum_samples
        self.minimum_wilson_lower_bound = float(minimum_wilson_lower_bound)
        self.rollback_enabled = rollback_enabled
        self.high_risk_excluded = high_risk_excluded
        self.rules = normalized

    @classmethod
    def load(cls, path: str | Path) -> "MarginalValuePolicy":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MarginalValueError("stop policy is unreadable") from exc
        expected = {"schema_version", "enabled", "mode", "policy_revision", "minimum_samples",
                    "minimum_wilson_lower_bound", "rollback_enabled", "high_risk_excluded", "rules"}
        if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != "1.0":
            raise MarginalValueError("stop policy is not a supported closed schema")
        return cls(**{key: value[key] for key in expected - {"schema_version"}})

    def decide(self, *, task_tier: str, rule: str, remaining_budget: int | float,
               expected_success_gain: int | float,
               promotion: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if rule not in RULES:
            raise MarginalValueError("unknown stop rule")
        if type(remaining_budget) not in {int, float} or remaining_budget < 0:
            raise MarginalValueError("remaining budget is invalid")
        if type(expected_success_gain) not in {int, float} or not 0 <= expected_success_gain <= 1:
            raise MarginalValueError("expected success gain is invalid")
        if not self.enabled or not self.rules[rule]["enabled"]:
            return {"decision": "CONTINUE", "reason": "RULE_DISABLED", "rule": rule}
        if self.high_risk_excluded and task_tier == "high_risk":
            return {"decision": "CONTINUE", "reason": "HIGH_RISK_EXCLUDED", "rule": rule}
        should_stop = remaining_budget <= 0 or expected_success_gain < self.rules[rule]["minimum_expected_success_gain"]
        if not should_stop:
            return {"decision": "CONTINUE", "reason": "MARGINAL_VALUE_AVAILABLE", "rule": rule}
        reason = "BUDGET_UNAVAILABLE" if remaining_budget <= 0 else "INSUFFICIENT_EXPECTED_VALUE"
        if self.mode == "shadow":
            return {"decision": "SHADOW_STOP", "reason": reason, "rule": rule}
        if not isinstance(promotion, Mapping) or set(promotion) != PROMOTION_KEYS:
            raise MarginalValueError("enforcement requires a closed promotion artifact")
        corpus = promotion.get("corpus_sha256")
        if (promotion["decision"] != "PROMOTE" or promotion["policy_revision"] != self.policy_revision
                or promotion["synthetic"] is not False or promotion.get("quality_non_regression") is not True
                or not isinstance(corpus, str) or len(corpus) != 64):
            raise MarginalValueError("enforcement promotion is not valid")
        return {"decision": "ENFORCED_STOP", "reason": reason, "rule": rule}


def safe_decide(policy: MarginalValuePolicy, **kwargs: Any) -> dict[str, Any]:
    rule = str(kwargs.get("rule", "unknown"))
    try:
        return policy.decide(**kwargs)
    except Exception as exc:
        return {"decision": "CONTINUE", "reason": "RULE_DISABLED_ERROR", "rule": rule,
                "disabled_rule": rule, "error_type": type(exc).__name__}
