"""Evidence-gated execution selection, independent of review depth."""
from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .autonomy_intent import detect_autonomous_local_intent
from .errors import CoreError
from .execution_value_router import ExecutionValuePolicy
from .policy import CorePolicy, TIERS


LANES = {"T0", "T1", "T2", "T3", "T4"}
BOOL_FACTS = {"changes_repository", "requires_omp", "requires_delegation",
              "autonomous", "multi_system"}


def safe_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("declared_paths must be a list")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("paths must be nonempty strings")
        path = PurePosixPath(item.replace("\\", "/"))
        if path.is_absolute() or PureWindowsPath(item).drive or ".." in path.parts or str(path) == ".":
            raise ValueError("paths must be project-relative files")
        result.append(str(path))
    return sorted(set(result))


def select_prime_lane(task: Any, task_tier: str, policy: CorePolicy) -> dict[str, Any]:
    if not isinstance(task_tier, str) or task_tier not in TIERS:
        raise CoreError("INVALID_TASK_TIER", "Task tier is not supported.")
    if not isinstance(task, dict) or set(task) - (BOOL_FACTS | {
        "text", "declared_paths", "semantic_assertions", "risk_tier"
    }):
        raise CoreError("ROUTING_FACTS_INVALID", "Task must contain supported routing facts.")
    if any(type(task[key]) is not bool for key in BOOL_FACTS & task.keys()):
        raise CoreError("ROUTING_FACTS_INVALID", "Routing flags must be booleans.")
    text = task.get("text", "")
    assertions = task.get("semantic_assertions", [])
    risk = task.get("risk_tier", "high" if task_tier == "high_risk" else "moderate")
    if (not isinstance(text, str) or not isinstance(risk, str) or risk not in {"low", "moderate", "high"}
            or not isinstance(assertions, list)
            or any(not isinstance(item, str) or not item.strip() for item in assertions)):
        raise CoreError("ROUTING_FACTS_INVALID", "Risk, task text, or assertions are invalid.")
    try:
        paths = safe_paths(task.get("declared_paths", []))
    except ValueError as exc:
        raise CoreError("ROUTING_FACTS_INVALID", str(exc)) from exc
    autonomous = task.get("autonomous", False) or detect_autonomous_local_intent(text)["activated"]
    if autonomous:
        recommended, reason = "T4", "AUTONOMOUS_INTENT_REQUIRES_DURABLE_LOOP"
    elif task_tier in {"high_risk", "complex"} or task.get("multi_system") or task.get("requires_delegation"):
        recommended, reason = "T3", "PRIME_REASONING_OR_REVIEW_REQUIRED"
    elif not text.strip() or not paths:
        recommended, reason = "T3", "INSUFFICIENT_EVIDENCE_FOR_LOWER_LANE"
    else:
        execution = ExecutionValuePolicy({
            "schema_version": "1.0", "mode": "gated",
            "promoted_lanes": ["T0_DETERMINISTIC", "T1_NATIVE"],
            "maximum_escalations": 1,
            "omp_lite": {"maximum_files": 2, "allowed_risk_tiers": ["low"],
                         "require_semantic_assertion": False},
        }).decide(task=text, declared_paths=paths, risk_tier=risk,
                  semantic_assertions=assertions)
        recommended = execution.recommended_lane.split("_")[0]
        reason = execution.reason
        if task.get("requires_omp") and recommended in {"T0", "T1"}:
            recommended, reason = "T2", "BOUNDED_LOW_RISK_OMP_WITH_HOST_VERIFICATION"
    actual = policy.t2_actual_lane if recommended == "T2" else recommended
    return {
        "recommended_lane": recommended, "actual_lane": actual, "rationale": reason,
        "policy_override": actual != recommended,
        "actual_lane_reason": reason if actual == recommended else "RECOMMENDED_LANE_NOT_PROMOTED",
        "parallel_safe": actual in {"T0", "T1"} and len(paths) == 1,
        "policy_fingerprint": policy.fingerprint,
        "classification_only": True, "execution_started": False, "model_calls": 0,
    }
