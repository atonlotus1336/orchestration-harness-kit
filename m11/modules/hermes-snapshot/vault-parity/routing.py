"""M7 routing policy helpers for vault-parity."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

DEFAULT_POLICY_PATH = Path("${HERMES_ROUTING_POLICY_PATH}")


class MeteredRouteRefusal(ValueError):
    def __init__(self, alias: str):
        self.alias = alias
        super().__init__(f"REFUSAL: metered route alias '{alias}' requires --approve-metered")


def policy_path() -> Path:
    return Path(os.environ.get("myvault_ROUTING_POLICY", str(DEFAULT_POLICY_PATH)))


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    selected = Path(path) if path else policy_path()
    with selected.open("r", encoding="utf-8") as fh:
        policy = json.load(fh)
    if not policy.get("prompt_classes") or not policy.get("default_route"):
        raise ValueError("routing policy missing prompt_classes/default_route")
    return policy


def _prompt_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_prompt_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_prompt_text(v) for v in value)
    return str(value)


def classify_prompt(prompt: Any, policy: dict[str, Any] | None = None) -> str:
    policy = policy or load_policy()
    text = _prompt_text(prompt).lower()
    for name in ("code", "vision", "deep-reason", "cheap"):
        cfg = policy["prompt_classes"].get(name, {})
        if any(_matches_term(text, str(term).lower()) for term in cfg.get("match", [])):
            return name
    return "default"


def _matches_term(text: str, term: str) -> bool:
    if " " in term:
        return term in text
    return re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", text) is not None


def _route_for_class(prompt_class: str, policy: dict[str, Any]) -> dict[str, Any]:
    if prompt_class == "default":
        route = dict(policy["default_route"])
    else:
        route = dict(policy["prompt_classes"][prompt_class])
    route["prompt_class"] = prompt_class
    route["route"] = f"{route['provider']}:{route['model']}"
    route["policy_version"] = policy.get("policy_version", "unknown")
    return route


def resolve_route_request(requested: str, approve_metered: bool = False, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    alias = str(requested or "").strip().lower()
    if alias and not approve_metered:
        for metered in policy.get("metered_aliases", []):
            if metered in alias:
                raise MeteredRouteRefusal(alias)
    if alias in policy.get("prompt_classes", {}):
        return _route_for_class(alias, policy)
    if alias in ("", "default"):
        return _route_for_class("default", policy)
    provider, _, model = alias.partition(":")
    return {
        "prompt_class": "manual",
        "provider": provider or policy["default_route"]["provider"],
        "model": model or alias,
        "route": alias,
        "tier_weight": 2,
        "policy_version": policy.get("policy_version", "unknown"),
    }


def select_route(prompt: Any = "", prompt_class: str | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    selected_class = prompt_class or classify_prompt(prompt, policy)
    return _route_for_class(selected_class, policy)


def dispatch_args(route: dict[str, Any]) -> dict[str, str]:
    return {"provider": str(route["provider"]), "model": str(route["model"]), "route": str(route["route"])}


def route_ledger_path(policy: dict[str, Any] | None = None) -> Path:
    policy = policy or load_policy()
    return Path(os.environ.get("myvault_ROUTE_LEDGER", policy["route_ledger"]))


def log_route(
    prompt_class: str,
    route: str,
    wall_clock: float,
    tier_weight: int,
    outcome: str,
    *,
    surface: str = "",
    session_id: str = "",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or load_policy()
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": "M7",
        "prompt_class": prompt_class,
        "route": route,
        "wall_clock": round(float(wall_clock or 0), 3),
        "tier_weight": int(tier_weight),
        "outcome": outcome,
        "surface": surface,
        "session_id": session_id,
        "policy_version": policy.get("policy_version", "unknown"),
    }
    path = route_ledger_path(policy)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")
    return row


def route_hint(kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    kwargs = kwargs or {}
    policy = load_policy()
    prompt = kwargs.get("prompt") or kwargs.get("message") or kwargs.get("user_message") or kwargs.get("messages") or ""
    requested = kwargs.get("route") or kwargs.get("route_hint") or kwargs.get("requested_route")
    if requested:
        route = resolve_route_request(str(requested), bool(kwargs.get("approve_metered")), policy)
    else:
        route = select_route(prompt, kwargs.get("prompt_class"), policy)
    wall_clock = float(kwargs.get("wall_clock") or kwargs.get("wall_clock_s") or 0)
    outcome = str(kwargs.get("outcome") or "selected")
    row = log_route(
        route["prompt_class"],
        route["route"],
        wall_clock,
        int(route.get("tier_weight", 1)),
        outcome,
        surface=str(kwargs.get("platform") or kwargs.get("surface") or ""),
        session_id=str(kwargs.get("session_id") or ""),
        policy=policy,
    )
    return {**route, "outcome": outcome, "ledger_row": row, "ledger_path": str(route_ledger_path(policy))}


def escalation_decision(
    *,
    deterministic_failure: bool = False,
    judge_rejected: bool = False,
    lane_scores: list[float] | None = None,
    confidence: float | None = None,
    prior_escalations: int = 0,
    max_escalations: int = 2,
    disagreement_threshold: float = 25.0,
    tier_capacity_ok: bool = True,
) -> dict[str, Any]:
    """Q12 escalation controller: deterministic/judge/disagreement only."""
    lane_scores = lane_scores or []
    if prior_escalations >= max_escalations:
        return {"escalate": False, "signal": "cap_reached", "prior_escalations": prior_escalations}
    if not tier_capacity_ok:
        return {"escalate": False, "signal": "tier_capacity_blocked", "prior_escalations": prior_escalations}
    if deterministic_failure:
        return {"escalate": True, "signal": "deterministic_verification_failure", "prior_escalations": prior_escalations}
    if judge_rejected:
        return {"escalate": True, "signal": "judge_rejection", "prior_escalations": prior_escalations}
    if len(lane_scores) >= 2 and (max(lane_scores) - min(lane_scores)) >= disagreement_threshold:
        return {"escalate": True, "signal": "inter_lane_disagreement", "prior_escalations": prior_escalations}
    return {"escalate": False, "signal": "no_trusted_signal", "prior_escalations": prior_escalations, "confidence_ignored": confidence is not None}


def council_preset(name: str, surface: str = "cli", policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    preset = dict(policy["council_presets"][name])
    preset["name"] = name
    preset["surface"] = surface
    preset["lane_metadata"] = {
        "planner": preset["planner"],
        "solvers": list(preset["solvers"]),
        "reviewer": preset["reviewer"],
        "judge": preset["judge"],
        "self_review_passes": preset["self_review_passes"],
        "concurrency_cap": preset["concurrency_cap"],
        "cost_tier": preset["cost_tier"],
    }
    return preset


def assert_no_council_family_collision(preset: dict[str, Any]) -> None:
    judge = str(preset["judge"]).split(":")[0].lower()
    solvers = [str(s).split(":")[0].lower() for s in preset["solvers"]]
    for family in set(solvers):
        if solvers.count(family) > len(solvers) / 2 and judge == family:
            raise AssertionError(f"judge family {judge} matches majority solver family")

