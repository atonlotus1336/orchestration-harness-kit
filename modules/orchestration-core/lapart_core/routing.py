"""Deterministic T0–T4 routing with the governed T2-to-T3 policy."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import CoreError
from .policy import CorePolicy, TIERS


LANES = {"T0", "T1", "T2", "T3", "T4"}


def select_prime_lane(task: Any, task_tier: str, policy: CorePolicy) -> dict[str, Any]:
    if task_tier not in TIERS:
        raise CoreError("INVALID_TASK_TIER", "Task tier is not supported.")
    facts: Mapping[str, Any] = task if isinstance(task, Mapping) else {}
    allowed = {
        "changes_repository",
        "requires_omp",
        "requires_delegation",
        "autonomous",
        "multi_system",
    }
    unknown = set(facts) - allowed
    if unknown:
        raise CoreError(
            "ROUTING_FACTS_INVALID",
            "Routing facts contain unknown keys.",
            details={"unknown": sorted(unknown)},
        )
    if any(type(value) is not bool for value in facts.values()):
        raise CoreError("ROUTING_FACTS_INVALID", "Routing facts must be booleans.")

    if facts.get("autonomous") or facts.get("multi_system") or task_tier == "high_risk":
        recommended = "T4"
        reason = "autonomous_or_high_risk"
    elif facts.get("requires_delegation") or task_tier == "complex":
        recommended = "T3"
        reason = "governed_delegation"
    elif facts.get("requires_omp"):
        recommended = "T2"
        reason = "governed_worker_requested"
    elif facts.get("changes_repository") or task_tier == "standard":
        recommended = "T1"
        reason = "native_agent_work"
    else:
        recommended = "T0"
        reason = "direct_tools"
    actual = policy.t2_actual_lane if recommended == "T2" else recommended
    return {
        "recommended_lane": recommended,
        "actual_lane": actual,
        "rationale": reason,
        "policy_fingerprint": policy.fingerprint,
    }
