"""Coordinate selected reviewers while preserving report-only and S4 gates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Callable, Mapping

from .adapters import ReportOnlyProvider, ReviewOutcome, ReviewerAdapter
from .catalog import Catalog
from .models import Pipeline, Verdict
from .transport import JsonReviewTransport, ReviewBudgetTracker, BudgetViolation
from .policy import load_policy
from .prompts import review_prompt, finding_schema as canonical_schema
from .proofs import HostTestVerifier
from .reconcile import Reconciliation, reconcile_outputs


@dataclass(frozen=True)
class RunResult:
    verdict: Verdict
    outcomes: tuple[ReviewOutcome, ...]
    reconciliation: Reconciliation | None
    controls: tuple[str, ...]
    invoked_reviewers: tuple[str, ...]
    budget: Mapping[str, object] | None = None
    proof_receipts: tuple[dict, ...] = ()


class Runner:
    def __init__(self, catalog: Catalog, finding_schema: Mapping[str, object], blocking_proofs: tuple[str, ...]) -> None:
        self.catalog = catalog
        self.finding_schema = canonical_schema()
        self.schema_compatible = not finding_schema or dict(finding_schema) == self.finding_schema
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
        proof_verifier: HostTestVerifier | None = None,
        legacy_authoritative: bool = True,
        adaptive_authoritative: bool = False,
        budget_limits: Mapping[str, int] | None = None,
    ) -> RunResult:
        if not self.schema_compatible:
            return self._blocked("finding schema does not match the owned response contract")
        if not contents or any(not isinstance(k, str) or not isinstance(v, str) for k, v in contents.items()):
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
        limits = dict(policy["budgets"][pipeline.value])
        if budget_limits is not None:
            if set(budget_limits) != set(limits) or any(
                type(value) is not int or not 0 <= value <= limits[key]
                for key, value in budget_limits.items()
            ):
                return self._blocked("remaining budget cannot increase policy limits")
            limits = dict(budget_limits)
        if len(selected) > limits["max_model_calls"]:
            return self._blocked("remaining model-call budget cannot admit the complete reviewer set")
        if len(contents) > limits["max_files"]:
            return self._blocked("review scope exceeds file budget")
        if sum(len(v.encode()) for v in contents.values()) > limits["max_context_bytes"]:
            return self._blocked("review context exceeds byte budget")
        prepared = {}
        try:
            tracker = ReviewBudgetTracker(limits)
        except BudgetViolation:
            return self._blocked("invalid aggregate review budget")
        # Admit the entire reviewer set before the first paid request.
        try:
            expected = {p: hashlib.sha256(v.encode()).hexdigest() for p, v in contents.items()}
            if expected != dict(source_hashes) or dict(hash_provider(tuple(sorted(contents)))) != expected:
                return self._blocked("review content or current source hashes do not match")
            if proof_verifier is not None:
                if type(proof_verifier) is not HostTestVerifier:
                    return self._blocked("unowned proof verifier")
                proof_verifier.begin(expected)
            total_estimate = 0
            for reviewer in selected:
                tracker.remaining()
                if Path(reviewer.skill_path).stat().st_size > limits["max_context_bytes"]:
                    return self._blocked("reviewer skill exceeds context budget")
                if not self.catalog.verify_skill(reviewer.reviewer_id):
                    return self._blocked("selected mandatory reviewer skill is unverified")
                provider = providers.get(reviewer.reviewer_id)
                if provider is None:
                    return self._blocked("selected reviewer provider is missing")
                if not provider.attestation.report_only:
                    return self._blocked("selected reviewer provider is not report-only")
                if type(provider.transport) is not JsonReviewTransport or provider.invoke is not None:
                    return self._blocked("selected reviewer has no owned bounded transport")
                provider.transport.validate()
                capability = self.catalog.capability_for(reviewer.reviewer_id)
                if set(capability.readable_files) != set(contents) or capability.allowed_commands or capability.allowed_tests:
                    return self._blocked("reviewer capability does not match the report-only scope")
                skill_bytes = Path(reviewer.skill_path).read_bytes()
                if hashlib.sha256(skill_bytes).hexdigest() != reviewer.skill_sha256:
                    return self._blocked("reviewer skill changed during preflight")
                skill = review_prompt(reviewer.reviewer_id, reviewer.lens, self.finding_schema,
                                      proof_verifier.descriptions() if proof_verifier else ())
                size = len(provider.transport.payload(skill, contents, limits["max_output_tokens"]))
                if size > limits["max_context_bytes"]:
                    return self._blocked("complete reviewer packet exceeds byte budget")
                total_estimate += size
                prepared[reviewer.reviewer_id] = (provider, capability, skill)
            if total_estimate > limits["max_estimated_input_tokens"]:
                return self._blocked("complete reviewer set exceeds aggregate estimated input budget")
            tracker.remaining()
        except (OSError, ValueError, TypeError, KeyError):
            return self._blocked("reviewer preflight failed")
        outcomes: list[ReviewOutcome] = []
        invoked: list[str] = []
        for reviewer in selected:
            provider, capability, skill = prepared[reviewer.reviewer_id]
            invoked.append(reviewer.reviewer_id)
            adapter = ReviewerAdapter(capability, contents=contents, source_hashes=source_hashes, hash_provider=hash_provider)
            outcome = adapter.run(provider, budget=tracker, skill=skill)
            outcomes.append(outcome)
            if outcome.verdict is Verdict.INCONCLUSIVE:
                break
        if any(outcome.verdict is Verdict.INCONCLUSIVE for outcome in outcomes):
            return RunResult(Verdict.INCONCLUSIVE, tuple(outcomes), None,
                ("reviewer execution incomplete",), tuple(invoked), tracker.public())
        parts = []
        try:
            for reviewer_id, outcome in zip(invoked, outcomes):
                parts.append(reconcile_outputs(outcome.findings, self.finding_schema,
                    self.blocking_proofs, expected_reviewer_id=reviewer_id,
                    proof_verifier=proof_verifier, budget=tracker))
            if dict(hash_provider(tuple(sorted(contents)))) != expected:
                raise ValueError("source changed during proof verification")
            tracker.remaining()
        except (OSError, ValueError, TypeError):
            tracker.failed = True
            return RunResult(Verdict.INCONCLUSIVE, tuple(outcomes), None,
                ("host proof verification failed",), tuple(invoked), tracker.public(),
                tuple(proof_verifier.receipts) if proof_verifier else ())
        reconciliation = Reconciliation(
            tuple(f for part in parts for f in part.findings),
            tuple(f for part in parts for f in part.rejected),
            tuple(error for part in parts for error in part.errors))
        if (len(outcomes) != len(selected) or not required_ids.issubset(invoked)
                or not tracker.complete or tracker.failed
                or any(item.verdict is Verdict.INCONCLUSIVE for item in outcomes) or reconciliation.errors):
            verdict = Verdict.INCONCLUSIVE
        elif reconciliation.findings:
            verdict = Verdict.FINDINGS
        else:
            verdict = Verdict.PASS
        return RunResult(verdict, tuple(outcomes), reconciliation, (), tuple(invoked), tracker.public(),
                         tuple(proof_verifier.receipts) if proof_verifier else ())

    @staticmethod
    def _blocked(reason: str) -> RunResult:
        return RunResult(Verdict.INCONCLUSIVE, (), None, (reason,), ())
