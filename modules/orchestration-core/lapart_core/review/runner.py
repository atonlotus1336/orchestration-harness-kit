"""Coordinate selected reviewers while preserving report-only and S4 gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .adapters import ReportOnlyProvider, ReviewOutcome, ReviewerAdapter
from .catalog import Catalog
from .models import Pipeline, Verdict
from .policy import load_policy
from .reconcile import Reconciliation, reconcile_outputs


@dataclass(frozen=True)
class RunResult:
    verdict: Verdict
    outcomes: tuple[ReviewOutcome, ...]
    reconciliation: Reconciliation | None
    controls: tuple[str, ...]
    invoked_reviewers: tuple[str, ...]


class Runner:
    def __init__(self, catalog: Catalog, finding_schema: Mapping[str, object], blocking_proofs: tuple[str, ...]) -> None:
        self.catalog = catalog
        self.finding_schema = finding_schema
        self.blocking_proofs = blocking_proofs

    def run(
        self,
        *,
        pipeline: Pipeline,
        triggers: tuple[str, ...],
        contents: Mapping[str, str],
        source_hashes: Mapping[str, str],
        hash_provider: Callable[[tuple[str, ...]], Mapping[str, str]],
        providers: Mapping[str, ReportOnlyProvider],
        s4_confirmed: bool = False,
        legacy_authoritative: bool = True,
        adaptive_authoritative: bool = False,
    ) -> RunResult:
        if not contents:
            return self._blocked("empty scope")
        if legacy_authoritative and adaptive_authoritative:
            return self._blocked("duplicate authoritative review owners")
        if pipeline is Pipeline.S0:
            return self._blocked("S0 requires the deterministic evidence path; reviewer calls are forbidden")
        policy = load_policy().data
        members = policy["pipelines"][pipeline.value]
        selected = self.catalog.triggered(triggers)
        fixed_reviewers = {
            member for pipeline_members in policy["pipelines"].values()
            for member in pipeline_members
            if member not in {"facts", "deterministic_checks", "receipt", "triggered_specialist"}
        }
        if any(
            reviewer.reviewer_id not in members
            and (reviewer.reviewer_id in fixed_reviewers or "triggered_specialist" not in members)
            for reviewer in selected
        ):
            return self._blocked("triggered reviewer is outside the selected pipeline; reevaluate review depth")
        required_ids = set(members) & fixed_reviewers
        if required_ids - self.catalog.reviewers.keys():
            return self._blocked("required pipeline reviewers are missing from the catalog")
        selected_by_id = {reviewer.reviewer_id: reviewer for reviewer in selected}
        selected_by_id.update({key: self.catalog.reviewers[key] for key in required_ids})
        selected = tuple(selected_by_id[key] for key in sorted(selected_by_id))
        if not selected:
            return self._blocked("no reviewers selected; review has not run")
        if len(selected) > policy["budgets"][pipeline.value]["max_model_calls"]:
            return self._blocked("selected reviewers exceed pipeline model-call budget")
        if any(reviewer.requires_confirmation for reviewer in selected) and not s4_confirmed:
            return self._blocked("selected reviewer requires confirmation")
        if pipeline is Pipeline.S4 and not s4_confirmed:
            return self._blocked("S4 confirmation required")
        if any(not self.catalog.verify_skill(item.reviewer_id) for item in selected):
            return self._blocked("selected reviewer skill is unverified")
        if any(item.reviewer_id not in providers for item in selected):
            return self._blocked("selected reviewer provider is missing")
        if len(contents) > policy["budgets"][pipeline.value]["max_files"]:
            return self._blocked("review scope exceeds the selected pipeline budget")
        outcomes: list[ReviewOutcome] = []
        invoked: list[str] = []
        for reviewer in selected:
            if not self.catalog.verify_skill(reviewer.reviewer_id):
                if reviewer.required:
                    outcomes.append(ReviewOutcome(Verdict.INCONCLUSIVE, violations=(f"unverified reviewer: {reviewer.reviewer_id}",)))
                continue
            provider = providers.get(reviewer.reviewer_id)
            if provider is None:
                outcomes.append(ReviewOutcome(Verdict.INCONCLUSIVE, violations=(f"missing reviewer provider: {reviewer.reviewer_id}",)))
                continue
            invoked.append(reviewer.reviewer_id)
            adapter = ReviewerAdapter(self.catalog.capability_for(reviewer.reviewer_id), contents=contents, source_hashes=source_hashes, hash_provider=hash_provider)
            outcomes.append(adapter.run(provider))
        reconciliation = reconcile_outputs(
            (finding for outcome in outcomes for finding in outcome.findings),
            self.finding_schema,
            self.blocking_proofs,
        )
        if any(item.verdict is Verdict.INCONCLUSIVE for item in outcomes) or reconciliation.errors:
            verdict = Verdict.INCONCLUSIVE
        elif reconciliation.findings:
            verdict = Verdict.FINDINGS
        else:
            verdict = Verdict.PASS
        return RunResult(verdict, tuple(outcomes), reconciliation, (), tuple(invoked))

    @staticmethod
    def _blocked(reason: str) -> RunResult:
        return RunResult(Verdict.INCONCLUSIVE, (), None, (reason,), ())
