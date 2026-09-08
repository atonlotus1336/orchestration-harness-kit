"""Closed, provider-free review classification for all harness adapters."""
from dataclasses import asdict
from .context import ReviewFact
from .models import Scope
from .policy import load_policy
from .router import select_review, scope_review_facts, structured_review_facts
from ..errors import CoreError
from ..routing import safe_paths


def classify_review(raw, *, declared_paths):
    if raw is None:
        raw = {}
    allowed = {"scope", "required_level", "changed_lines", "subsystem_count",
               "s4_confirmed", "facts", "routing_facts"}
    try:
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("review accepts scope facts, never an execution lane")
        paths = safe_paths(declared_paths)
        confirmed = raw.get("s4_confirmed", False)
        if type(confirmed) is not bool:
            raise ValueError("s4_confirmed must be boolean")
        policy = load_policy()
        facts = [ReviewFact("requested", raw.get("required_level", "S0"))]
        supplied = raw.get("facts", [])
        if not isinstance(supplied, list):
            raise ValueError("review facts must be a list")
        for item in supplied:
            if not isinstance(item, dict) or set(item) != {"key", "required_level", "known", "load_bearing"}:
                raise ValueError("each review fact needs key, required_level, known, load_bearing")
            if (not isinstance(item["key"], str) or not item["key"].strip()
                    or not isinstance(item["required_level"], str)
                    or type(item["known"]) is not bool or type(item["load_bearing"]) is not bool):
                raise ValueError("invalid review fact types")
            facts.append(ReviewFact(**item, provenance="caller_reported"))
        facts.extend(structured_review_facts(raw.get("routing_facts"), changed_lines=raw.get("changed_lines"), subsystem_count=raw.get("subsystem_count")))
        if not paths:
            facts.append(ReviewFact("changed_scope_unobserved", "S0", load_bearing=True, known=False))
        facts.extend(scope_review_facts(
            paths, policy, changed_lines=raw.get("changed_lines"),
            subsystem_count=raw.get("subsystem_count"),
        ))
        decision = select_review(facts, policy, Scope(raw.get("scope", "phase")), s4_confirmed=confirmed)
        return {
            "scope": decision.scope.value, "pipeline": decision.pipeline.value,
            "routing_verdict": decision.verdict.value,
            "reasons": list(decision.reasons), "exclusions": list(decision.exclusions),
            "requires_confirmation": decision.requires_confirmation,
            "runnable": decision.runnable, "budget": asdict(decision.budget),
            "policy_fingerprint": decision.policy_fingerprint,
            "classification_only": True, "review_completed": False,
            "reviewer_invoked": False, "model_calls": 0,
            "evidence_provenance": "caller_reported", "rollout_mode": policy.data["rollout_mode"],
        }
    except (ValueError, TypeError, KeyError) as exc:
        raise CoreError("REVIEW_FACTS_INVALID", str(exc)) from exc
