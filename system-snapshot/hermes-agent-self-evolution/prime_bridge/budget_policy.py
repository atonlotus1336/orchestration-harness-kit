"""Deterministic, conservative task-tier budget admission."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class BudgetPolicyError(RuntimeError):
    pass


TIERS = ("atomic", "standard", "complex", "high_risk")
LIMIT_KEYS = (
    "max_context_bytes", "max_output_tokens", "max_wall_seconds",
    "max_tool_calls", "max_omp_repair_turns", "max_prime_review_bytes",
    "max_provider_calls",
)
DEMAND_TO_LIMIT = {
    "context_bytes": "max_context_bytes",
    "output_tokens": "max_output_tokens",
    "wall_seconds": "max_wall_seconds",
    "tool_calls": "max_tool_calls",
    "omp_repair_turns": "max_omp_repair_turns",
    "prime_review_bytes": "max_prime_review_bytes",
    "provider_calls": "max_provider_calls",
}
HIGH_RISK_MARKERS = frozenset({
    "credential", "secret", "security", "production", "deploy", "deployment",
    "delete", "destructive", "migration", "policy", "permission", "auth",
})
ATOMIC_MARKERS = frozenset({"typo", "rename", "one-line", "single line", "exact replace"})


class BudgetPolicy:
    def __init__(self, *, enabled: bool, tiers: Mapping[str, Mapping[str, int]]):
        if not isinstance(enabled, bool) or set(tiers) != set(TIERS):
            raise BudgetPolicyError("budget policy tiers are not closed")
        normalized: dict[str, dict[str, int]] = {}
        prior = {key: -1 for key in LIMIT_KEYS}
        for tier in TIERS:
            limits = tiers[tier]
            if not isinstance(limits, Mapping) or set(limits) != set(LIMIT_KEYS):
                raise BudgetPolicyError(f"{tier} limits are not closed")
            current: dict[str, int] = {}
            for key in LIMIT_KEYS:
                value = limits[key]
                minimum = 0 if key == "max_omp_repair_turns" else 1
                if type(value) is not int or value < minimum:
                    requirement = "a non-negative integer" if minimum == 0 else "a positive integer"
                    raise BudgetPolicyError(f"{tier}.{key} must be {requirement}")
                if value < prior[key]:
                    raise BudgetPolicyError(f"{tier}.{key} is not monotonic")
                current[key] = value
                prior[key] = value
            normalized[tier] = current
        self.enabled = enabled
        self.tiers = normalized

    @classmethod
    def load(cls, path: str | Path) -> "BudgetPolicy":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BudgetPolicyError("budget policy is unreadable") from exc
        if not isinstance(value, dict) or set(value) != {"schema_version", "enabled", "tiers"}:
            raise BudgetPolicyError("budget policy keys are not closed")
        if value["schema_version"] != "1.0":
            raise BudgetPolicyError("unsupported budget policy schema")
        return cls(enabled=value["enabled"], tiers=value["tiers"])

    def limits_for(self, tier: str) -> dict[str, int]:
        if tier not in self.tiers:
            raise BudgetPolicyError("unknown task tier")
        return dict(self.tiers[tier])

    def admit(self, tier: str, demand: Mapping[str, Any]) -> dict[str, Any]:
        limits = self.limits_for(tier)
        if not self.enabled:
            return {"decision": "ALLOWED", "reason": "POLICY_DISABLED", "tier": tier, "limits": limits}
        if not isinstance(demand, Mapping) or set(demand) - set(DEMAND_TO_LIMIT):
            raise BudgetPolicyError("budget demand keys are not closed")
        for key, value in demand.items():
            if type(value) not in {int, float} or value < 0:
                raise BudgetPolicyError(f"{key} demand must be non-negative")
            if value > limits[DEMAND_TO_LIMIT[key]]:
                return {"decision": "DENIED", "reason": "BUDGET_EXHAUSTED", "tier": tier, "limit": DEMAND_TO_LIMIT[key]}
        return {"decision": "ALLOWED", "reason": "BUDGET_AVAILABLE", "tier": tier, "limits": limits}


def classify_task(task: str, declared_paths: Sequence[str], requested_tier: str | None = None) -> str:
    if not isinstance(task, str) or not task.strip():
        raise BudgetPolicyError("task is required")
    if requested_tier is not None and requested_tier not in TIERS:
        raise BudgetPolicyError("requested tier is invalid")
    normalized = task.casefold()
    if any(marker in normalized for marker in HIGH_RISK_MARKERS):
        return "high_risk"
    inferred = "standard"
    if len(declared_paths) > 4 or len(task.encode("utf-8")) > 4096:
        inferred = "complex"
    elif len(declared_paths) == 1 and any(marker in normalized for marker in ATOMIC_MARKERS):
        inferred = "atomic"
    if requested_tier is None:
        return inferred
    return TIERS[max(TIERS.index(inferred), TIERS.index(requested_tier))]
