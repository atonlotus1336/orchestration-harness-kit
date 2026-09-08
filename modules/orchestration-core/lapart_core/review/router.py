"""Deterministic S0-S4 selection, separate from T0-T4 execution routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .context import ReviewBudget, ReviewFact
from .models import LoadedPolicy, Pipeline, Scope, Verdict

ORDER = {pipeline.value: index for index, pipeline in enumerate(Pipeline)}


@dataclass(frozen=True)
class ReviewDecision:
    scope: Scope
    pipeline: Pipeline
    verdict: Verdict
    reasons: tuple[str, ...]
    exclusions: tuple[str, ...]
    budget: ReviewBudget
    policy_fingerprint: str
    skill_loaded: bool
    requires_confirmation: bool
    runnable: bool


def _pipeline(value: str) -> Pipeline:
    try:
        return Pipeline(value)
    except ValueError as exc:
        raise ValueError(f"unknown required review pipeline: {value}") from exc


def select_review(
    facts: Iterable[ReviewFact],
    policy: LoadedPolicy,
    scope: Scope | str,
    *,
    s4_confirmed: bool = False,
) -> ReviewDecision:
    effective_scope = Scope(scope)
    scope_policy = policy.data["scopes"][effective_scope.value]
    allowed = tuple(Pipeline(item) for item in scope_policy["allowed"])
    minimum = Pipeline(scope_policy["minimum"])
    ordered = tuple(sorted(set(facts), key=lambda item: (item.key, item.provenance, item.required_level)))
    known_required = [minimum]
    reasons: list[str] = []
    exclusions: list[str] = []
    unknowns: list[ReviewFact] = []
    for fact in ordered:
        required = _pipeline(fact.required_level)
        if fact.load_bearing and not fact.known:
            unknowns.append(fact)
            continue
        known_required.append(required)
        if ORDER[required.value] > ORDER[minimum.value]:
            reasons.append(f"{fact.key}:{required.value}")
    candidate = max(known_required, key=lambda item: ORDER[item.value])
    inconclusive = False
    for fact in unknowns:
        base = _pipeline(fact.required_level)
        promoted_index = min(ORDER[base.value] + 1, ORDER[Pipeline.S4.value])
        promoted = Pipeline(f"S{promoted_index}")
        if promoted in allowed and ORDER[candidate.value] >= ORDER[promoted.value]:
            reasons.append(f"{fact.key}:unknown-covered:{candidate.value}")
        elif promoted in allowed:
            candidate = promoted
            reasons.append(f"{fact.key}:unknown-promoted:{promoted.value}")
        else:
            inconclusive = True
            exclusions.append(f"{fact.key}:unknown-load-bearing")
    if candidate not in allowed:
        inconclusive = True
        selected = max(allowed, key=lambda item: ORDER[item.value])
        exclusions.append(f"required-{candidate.value}-exceeds-{effective_scope.value}-ceiling")
    else:
        selected = candidate
    requires_confirmation = selected is Pipeline.S4 and not s4_confirmed
    if requires_confirmation:
        exclusions.append("S4-confirmation-required")
    verdict = Verdict.INCONCLUSIVE if inconclusive else Verdict.PASS
    budget = ReviewBudget.from_mapping(policy.data["budgets"][selected.value])
    skill_loaded = selected is not Pipeline.S0
    runnable = not inconclusive and not requires_confirmation
    return ReviewDecision(
        scope=effective_scope,
        pipeline=selected,
        verdict=verdict,
        reasons=tuple(reasons),
        exclusions=tuple(exclusions),
        budget=budget,
        policy_fingerprint=policy.fingerprint,
        skill_loaded=skill_loaded,
        requires_confirmation=requires_confirmation,
        runnable=runnable,
    )


def scope_review_facts(
    paths: Iterable[str], policy: LoadedPolicy, *,
    changed_lines: int | None = None, subsystem_count: int | None = None,
) -> tuple[ReviewFact, ...]:
    """Apply known scope thresholds without a model or claiming missing evidence.

    These are lower bounds, not proof of S0 eligibility. Callers must still
    provide behavioral/risk facts and independent verification evidence.
    """
    from pathlib import PurePosixPath
    canonical = tuple(sorted(set(paths)))
    thresholds = policy.data["selection"]["thresholds"]
    facts = []
    for key, value, limit_key in (
        ("file_count", len(canonical), "max_files"),
        ("changed_lines", changed_lines, "max_changed_lines"),
        ("subsystem_count", subsystem_count, "max_subsystems"),
    ):
        if value is None:
            continue
        if type(value) is not int or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        level = "S0"
        for current, higher in (("S0", "S1"), ("S1", "S2"), ("S2", "S3")):
            if value > thresholds[current][limit_key]:
                level = higher
        if key == "file_count" and value > thresholds["S3"]["hard_scope_ceiling_files"]:
            level = "S4"
        facts.append(ReviewFact(key, level, provenance=f"observed:{value}"))
    if subsystem_count is not None and subsystem_count >= 2:
        facts.append(ReviewFact("multiple_subsystems", "S3", provenance=f"observed:{subsystem_count}"))
    structural = {"auth", "security", "migration", "migrations", "schema", "schemas",
                  "database", "persistence", "concurrency", "policy", "policies",
                  "router", "routing"}
    for path in canonical:
        parsed = PurePosixPath(path.replace("\\", "/"))
        # Documentation filenames alone do not establish a code-risk trigger.
        if parsed.suffix.casefold() in {".md", ".rst", ".txt"}:
            continue
        tokens = set(parsed.parts) | set(parsed.stem.replace("-", "_").split("_"))
        tokens = {token.casefold() for token in tokens}
        if structural & tokens:
            facts.append(ReviewFact("structural_path", "S3", provenance=path))
        elif "tests" in tokens or "test" in tokens or "gates" in tokens:
            facts.append(ReviewFact("tests_or_gates_changed", "S2", provenance=path))
    return tuple(facts)


S0_FACTS = ("mechanical_or_exact", "independent_deterministic_validation",
            "no_unknown_load_bearing_fact", "no_risk_trigger")
RISK_FACTS = {
    "behavior_changed_with_incomplete_evidence": "S2", "tests_or_gates_changed": "S2",
    "maintenance_hotspot": "S2", "one_specialist_trigger": "S2",
    "multiple_subsystems": "S3", "public_interface": "S3", "architecture": "S3",
    "security": "S3", "migration": "S3", "persistence": "S3", "concurrency": "S3", "data_loss": "S3",
}

def structured_review_facts(observed=None, *, changed_lines=None, subsystem_count=None):
    """Typed classification facts; these never constitute a completed review."""
    observed = {} if observed is None else observed
    if not isinstance(observed, dict) or set(observed) - (set(S0_FACTS) | set(RISK_FACTS)):
        raise ValueError("unknown structured review fact")
    if any(type(value) is not bool for value in observed.values()):
        raise ValueError("structured review facts must be boolean")
    facts = []
    for key in S0_FACTS:
        if observed.get(key) is not True:
            facts.append(ReviewFact("s0_eligibility:" + key, "S0", provenance="missing_or_false", load_bearing=True, known=False))
    for key, value in (("changed_lines", changed_lines), ("subsystem_count", subsystem_count)):
        if value is None:
            facts.append(ReviewFact("s0_scope_unmeasured:" + key, "S0", load_bearing=True, known=False))
    for key, level in RISK_FACTS.items():
        if observed.get(key) is True:
            facts.append(ReviewFact(key, level, provenance="structured_observation", load_bearing=True))
    return tuple(facts)
