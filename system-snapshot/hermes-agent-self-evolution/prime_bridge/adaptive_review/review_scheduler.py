"""Deterministic bounded scheduling for report-only reviewer stages."""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Generic, Mapping, TypeVar

from .models import Pipeline, Verdict

T = TypeVar("T")

ACTIVATION_STATES = frozenset({
    "inactive", "shadow", "canary", "pilot_limited", "pilot_wider",
    "review", "production",
})


class ReviewSchedulingError(ValueError):
    pass


@dataclass(frozen=True)
class FrozenReviewStage:
    pipeline: str
    activation_state: str
    selected_members: tuple[str, ...]
    concurrency: int
    batches: tuple[tuple[str, ...], ...]
    reason_code: str
    grant_sha256s: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "pipeline": self.pipeline,
            "activation_state": self.activation_state,
            "selected_members": list(self.selected_members),
            "concurrency": self.concurrency,
            "batches": [list(batch) for batch in self.batches],
            "reason_code": self.reason_code,
            "grant_sha256s": dict(sorted(self.grant_sha256s.items())),
        }


def plan_review_stage(
    *,
    pipeline: Pipeline,
    selected_members: tuple[str, ...],
    parallel_policy: Mapping[str, object],
    max_model_calls: int,
    host_slots: int | None = None,
    execution_slots: int = 0,
    convergence_reserve: int = 1,
) -> FrozenReviewStage:
    if len(selected_members) != len(set(selected_members)):
        raise ReviewSchedulingError("review stage members must be unique")
    if any(not isinstance(member, str) or not member for member in selected_members):
        raise ReviewSchedulingError("review stage member identity is invalid")
    activation = parallel_policy.get("activation_state", "inactive")
    if activation not in ACTIVATION_STATES:
        raise ReviewSchedulingError("unknown review activation state")
    if pipeline is Pipeline.S0:
        width = 0
    elif activation in {"inactive", "shadow"}:
        width = 1 if selected_members else 0
    elif parallel_policy.get("qualification_verified") is not True:
        width = 1 if selected_members else 0
    else:
        max_by_level = parallel_policy.get("max_concurrent_reviewers", {})
        initial_by_level = parallel_policy.get("initial_ceiling", {})
        if not isinstance(max_by_level, Mapping) or not isinstance(initial_by_level, Mapping):
            raise ReviewSchedulingError("review concurrency policy is incomplete")
        ceiling = max_by_level.get(pipeline.value)
        initial = initial_by_level.get(pipeline.value)
        if type(ceiling) is not int or type(initial) is not int or not 0 <= initial <= ceiling:
            raise ReviewSchedulingError("review concurrency ceiling is invalid")
        qualified_ceiling = (
            ceiling if activation in {"pilot_wider", "production"} else initial
        )
        if activation in {"pilot_limited", "pilot_wider"}:
            lineage = parallel_policy.get("lineage_sha256")
            if not isinstance(lineage, str) or re.fullmatch(r"[0-9a-f]{64}", lineage) is None:
                qualified_ceiling = 1
            else:
                fraction = .10 if activation == "pilot_limited" else .50
                if int(lineage[:8], 16) / 0x100000000 >= fraction:
                    qualified_ceiling = 1
        capacity = qualified_ceiling if host_slots is None else max(
            0, host_slots - execution_slots - convergence_reserve,
        )
        width = min(len(selected_members), max_model_calls, qualified_ceiling, capacity)
    if selected_members and width == 0:
        reason = "REVIEW_CAPACITY_RESERVED"
        batches = ()
    else:
        reason = "SERIAL_COMPATIBILITY" if width <= 1 else "ADAPTIVE_REVIEW"
        batches = tuple(
            selected_members[index:index + width]
            for index in range(0, len(selected_members), max(width, 1))
        )
    grants = {
        member: hashlib.sha256(json.dumps({
            "pipeline": pipeline.value,
            "activation_state": activation,
            "selected_members": selected_members,
            "member": member,
            "concurrency": width,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for member in selected_members
    }
    return FrozenReviewStage(
        pipeline.value, str(activation), selected_members, width, batches, reason, grants,
    )


def run_review_stage(
    stage: FrozenReviewStage,
    invoke: Callable[[str], T],
) -> tuple[tuple[str, T], ...]:
    """Run each frozen batch concurrently and publish results in member order."""
    if stage.selected_members and not stage.batches:
        raise ReviewSchedulingError("review stage has no admitted capacity")
    completed: dict[str, T] = {}
    for batch in stage.batches:
        if len(batch) == 1:
            member = batch[0]
            completed[member] = invoke(member)
        else:
            with ThreadPoolExecutor(
                max_workers=min(stage.concurrency, len(batch)),
                thread_name_prefix="adaptive-review",
            ) as pool:
                futures = {pool.submit(invoke, member): member for member in batch}
                for future in as_completed(futures):
                    completed[futures[future]] = future.result()
        if any(getattr(completed[member], "verdict", None) is Verdict.INCONCLUSIVE for member in batch):
            break
    return tuple(
        (member, completed[member])
        for member in stage.selected_members
        if member in completed
    )
