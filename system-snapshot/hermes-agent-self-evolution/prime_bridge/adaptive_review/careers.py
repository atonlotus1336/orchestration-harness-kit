"""Deterministic, zero-call career assignment for frozen reviewer panels."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Mapping

from .catalog import CAREER_KERNEL_VERSION, Catalog, CatalogError
from .models import Pipeline


class CareerAssignmentError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewerCall:
    reviewer_id: str
    obligations: tuple[str, ...]
    primary_obligations: tuple[str, ...] = ()

    @property
    def primaries(self) -> tuple[str, ...]:
        return self.primary_obligations or self.obligations


@dataclass(frozen=True)
class CareerQualification:
    reviewer_id: str
    career_id: str
    covered_obligations: tuple[str, ...]
    state: str
    career_kernel_version: str = CAREER_KERNEL_VERSION
    model_route: str = "default"
    workload_class: str = "default"
    layer_configuration: str = "career"
    expires_at_epoch: int | None = None
    enabled: bool = True


@dataclass(frozen=True)
class CareerBinding:
    reviewer_id: str
    career_id: str | None
    primary_method: str | None
    action: str
    reused: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CareerAssignment:
    pipeline: str
    desired_distinct_count: int
    actual_distinct_count: int
    bindings: tuple[CareerBinding, ...]
    reduction_reasons: tuple[str, ...]
    model_calls_added: int = 0

    @property
    def by_reviewer(self) -> dict[str, str | None]:
        return {binding.reviewer_id: binding.career_id for binding in self.bindings}


_STATE_PURPOSES = {
    "candidate": {"canary"},
    "canary_qualified": {"canary", "pilot"},
    "production_qualified": {"canary", "pilot", "production"},
    "disabled": set(),
}
_REASON_ORDER = (
    "disabled", "expired", "incompatible_contract", "method_overlap",
    "insufficient_calls", "no_qualified_pairing",
)


def _ordered_reasons(values: Iterable[str]) -> tuple[str, ...]:
    present = set(values)
    return tuple(reason for reason in _REASON_ORDER if reason in present)


def _eligible_for_call(catalog: Catalog, call: ReviewerCall,
    qualifications: tuple[CareerQualification, ...], *, purpose: str,
    career_enabled: bool, candidate_canary_authorized: bool, now_epoch: int,
    model_route: str, workload_class: str, layer_configuration: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not career_enabled:
        return (), ('disabled',)
    relevant = tuple(q for q in qualifications if q.reviewer_id == call.reviewer_id)
    if not relevant:
        return (), ('no_qualified_pairing',)
    eligible: set[str] = set()
    refused: set[str] = set()
    for qualification in relevant:
        if qualification.state not in _STATE_PURPOSES:
            raise CareerAssignmentError('unknown career qualification state')
        career = catalog.careers.get(qualification.career_id)
        if career is None:
            raise CareerAssignmentError('qualification references an unknown career')
        compatible = any(
            qualification.career_id in catalog.compatibility.get(obligation, ())
            for obligation in call.primaries
        )
        complete = set(call.obligations).issubset(qualification.covered_obligations)
        key_matches = (
            qualification.career_kernel_version == CAREER_KERNEL_VERSION
            and qualification.model_route == model_route
            and qualification.workload_class == workload_class
            and qualification.layer_configuration == layer_configuration
        )
        if not compatible or not complete or not key_matches:
            refused.add('incompatible_contract')
            continue
        if qualification.expires_at_epoch is not None and qualification.expires_at_epoch <= now_epoch:
            refused.add('expired')
            continue
        state_allowed = purpose in _STATE_PURPOSES[qualification.state]
        if qualification.state == 'candidate' and not candidate_canary_authorized:
            state_allowed = False
        if not qualification.enabled or not state_allowed:
            refused.add('disabled')
            continue
        eligible.add(qualification.career_id)
    if eligible:
        return tuple(sorted(eligible)), ()
    return (), _ordered_reasons(refused or {'no_qualified_pairing'})


def _target_count(catalog: Catalog, pipeline: str,
    calls: tuple[ReviewerCall, ...],
    fifth_distinct_triggered_obligation: str | None,
) -> int:
    desired = catalog.desired_distinct_careers[pipeline]
    if pipeline != 'S4' or fifth_distinct_triggered_obligation is None:
        return desired
    primaries = {item for call in calls for item in call.primaries}
    if (
        len(calls) >= 5
        and len(primaries) >= 5
        and fifth_distinct_triggered_obligation in primaries
    ):
        return 5
    return desired


def _solve(catalog: Catalog, calls: tuple[ReviewerCall, ...],
    eligible: tuple[tuple[str, ...], ...], target: int,
) -> tuple[str | None, ...]:
    @lru_cache(maxsize=None)
    def visit(index: int, selected_careers: frozenset[str],
        selected_methods: frozenset[str],
    ) -> tuple[tuple[int, int, int], tuple[str | None, ...]]:
        if index == len(calls):
            distinct = min(len(selected_methods), target)
            return (0, distinct, distinct * (distinct - 1) // 2), ()
        options: list[str | None] = [None]
        for career_id in eligible[index]:
            method = catalog.careers[career_id].primary_method
            if career_id in selected_careers:
                options.append(career_id)
            elif method not in selected_methods and len(selected_methods) < target:
                options.append(career_id)
        best: tuple[tuple[int, int, int], tuple[str | None, ...]] | None = None
        for career_id in options:
            if career_id is None:
                next_careers, next_methods = selected_careers, selected_methods
            else:
                next_careers = selected_careers | {career_id}
                next_methods = selected_methods | {catalog.careers[career_id].primary_method}
            score, suffix = visit(index + 1, next_careers, next_methods)
            score = (score[0] + int(career_id is not None), score[1], score[2])
            assignment = (career_id,) + suffix
            tie = tuple(value if value is not None else '~neutral' for value in assignment)
            if best is None:
                best = (score, assignment)
                continue
            best_tie = tuple(value if value is not None else '~neutral' for value in best[1])
            if score > best[0] or score == best[0] and tie < best_tie:
                best = (score, assignment)
        assert best is not None
        return best

    return visit(0, frozenset(), frozenset())[1]


def assign_careers(
    catalog: Catalog,
    pipeline: str | Pipeline,
    calls: Iterable[ReviewerCall],
    qualifications: Iterable[CareerQualification],
    *,
    purpose: str = 'production',
    career_enabled: bool = True,
    candidate_canary_authorized: bool = False,
    now_epoch: int = 0,
    model_route: str = 'default',
    workload_class: str = 'default',
    layer_configuration: str = 'career',
    previous_assignments: Mapping[str, str | None] | None = None,
    fifth_distinct_triggered_obligation: str | None = None,
) -> CareerAssignment:
    pipeline_id = pipeline.value if isinstance(pipeline, Pipeline) else pipeline
    if pipeline_id not in catalog.desired_distinct_careers:
        raise CareerAssignmentError('unknown review pipeline')
    if purpose not in {'canary', 'pilot', 'production'}:
        raise CareerAssignmentError('unknown career assignment purpose')
    if type(career_enabled) is not bool or type(candidate_canary_authorized) is not bool:
        raise CareerAssignmentError('career layer controls must be boolean')
    if type(now_epoch) is not int or now_epoch < 0:
        raise CareerAssignmentError('now_epoch must be a non-negative integer')
    frozen_calls = tuple(sorted(calls, key=lambda call: call.reviewer_id))
    if len(frozen_calls) > 7:
        raise CareerAssignmentError('career assignment exceeds seven selected calls')
    if len({call.reviewer_id for call in frozen_calls}) != len(frozen_calls):
        raise CareerAssignmentError('duplicate reviewer call')
    for call in frozen_calls:
        if (
            not call.reviewer_id or not call.obligations
            or len(set(call.obligations)) != len(call.obligations)
            or not set(call.primaries).issubset(call.obligations)
            or any(item not in catalog.lens_matrix for item in call.obligations)
        ):
            raise CareerAssignmentError('invalid reviewer obligation contract')
    frozen_qualifications = tuple(qualifications)
    qualification_keys = {
        (item.reviewer_id, item.career_id) for item in frozen_qualifications
    }
    if len(qualification_keys) != len(frozen_qualifications):
        raise CareerAssignmentError('duplicate career qualification')
    target = _target_count(
        catalog, pipeline_id, frozen_calls, fifth_distinct_triggered_obligation,
    )
    candidate_rows = tuple(
        _eligible_for_call(
            catalog, call, frozen_qualifications, purpose=purpose,
            career_enabled=career_enabled,
            candidate_canary_authorized=candidate_canary_authorized,
            now_epoch=now_epoch, model_route=model_route,
            workload_class=workload_class,
            layer_configuration=layer_configuration,
        )
        for call in frozen_calls
    )
    selected = _solve(
        catalog, frozen_calls, tuple(row[0] for row in candidate_rows), target,
    )
    previous = dict(previous_assignments or {})
    if set(previous) - {call.reviewer_id for call in frozen_calls}:
        raise CareerAssignmentError('previous assignment references an unknown call')
    bindings: list[CareerBinding] = []
    reductions: set[str] = set()
    seen_careers: set[str] = set()
    for call, career_id, (eligible, refused) in zip(
        frozen_calls, selected, candidate_rows,
    ):
        if career_id is None:
            reasons = refused or (('method_overlap',) if eligible else ('no_qualified_pairing',))
            reductions.update(reasons)
            bindings.append(CareerBinding(
                reviewer_id=call.reviewer_id, career_id=None,
                primary_method=None, action='omit',
                reasons=_ordered_reasons(reasons),
            ))
            continue
        prior = previous.get(call.reviewer_id)
        reused = career_id in seen_careers
        action = 'retain' if prior == career_id or reused else 'replace' if prior else 'add'
        bindings.append(CareerBinding(
            reviewer_id=call.reviewer_id,
            career_id=career_id,
            primary_method=catalog.careers[career_id].primary_method,
            action=action, reused=reused,
        ))
        seen_careers.add(career_id)
    actual = len({item for item in selected if item is not None})
    if len(frozen_calls) < target:
        reductions.add('insufficient_calls')
    if frozen_calls and actual == 0:
        reductions.add('no_qualified_pairing')
    return CareerAssignment(
        pipeline=pipeline_id,
        desired_distinct_count=target,
        actual_distinct_count=actual,
        bindings=tuple(bindings),
        reduction_reasons=_ordered_reasons(reductions),
    )
