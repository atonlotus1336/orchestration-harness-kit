"""Coordinate selected reviewers while preserving report-only and S4 gates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping

from .adapters import ReportOnlyProvider, ReviewOutcome, ReviewerAdapter
from .catalog import Catalog
from .careers import (
    CareerAssignmentError,
    CareerQualification,
    ReviewerCall,
    assign_careers,
)
from .models import Pipeline, Verdict
from .transport import ReviewBudgetTracker, BudgetViolation
from .transport_registry import is_admitted_report_only_transport
from .policy import load_policy
from .prompts import review_prompt, finding_schema as canonical_schema
from .receipts import ParallelReviewProvenance
from .review_scheduler import plan_review_stage, run_review_stage, ReviewSchedulingError
from .proofs import HostTestVerifier
from .packets import admit_packets, finalize_packets, PacketRefused
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
    packet_plan: Mapping[str, object] | None = None
    parallel_review: Mapping[str, object] | None = None


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
        s4_grant=None,
        proof_verifier: HostTestVerifier | None = None,
        legacy_authoritative: bool = True,
        adaptive_authoritative: bool = False,
        budget_limits: Mapping[str, int] | None = None,
        budget_grant=None,
        review_context=None,
        parallel_review: Mapping[str, object] | None = None,
        career_qualifications: tuple[CareerQualification, ...] = (),
        career_controls: Mapping[str, object] | None = None,
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
        automatic = False
        if s4_grant is not None:
            from .policy import validate_s4_grant
            try:
                automatic = pipeline is Pipeline.S4 and validate_s4_grant(s4_grant,source_hashes=source_hashes)
            except (ValueError,OSError):return self._blocked('invalid S4 policy authorization')
        if any(reviewer.requires_confirmation for reviewer in selected) and not (s4_confirmed or automatic):
            return self._blocked("selected reviewer requires confirmation")
        if pipeline is Pipeline.S4 and not (s4_confirmed or automatic):
            return self._blocked("S4 confirmation required")
        limits = dict(policy["budgets"][pipeline.value])
        approved_packets = None
        if budget_grant is not None:
            from .budget_exception import take_grant
            try:
                limits, approved_packets = take_grant(budget_grant, pipeline.value, source_hashes)
            except ValueError:
                return self._blocked("unowned or stale budget exception grant")
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
        parallel_policy = dict(parallel_review or policy.get("parallel_review", {}))
        parallel_active = parallel_policy.get("activation_state") in {"review", "production"}
        career_settings = {
            "career_enabled": False,
            "temperament_enabled": False,
            "career_shadow_pass": False,
            "purpose": "production",
            "candidate_canary_authorized": False,
            "now_epoch": 0,
            "model_route": "default",
            "workload_class": "default",
            "layer_configuration": "career",
        }
        if career_controls is not None:
            if set(career_controls) != set(career_settings):
                return self._blocked("career controls are not a closed contract")
            career_settings.update(dict(career_controls))
        if any(
            type(career_settings[name]) is not bool
            for name in (
                "career_enabled", "temperament_enabled",
                "career_shadow_pass", "candidate_canary_authorized",
            )
        ):
            return self._blocked("career layer controls must be boolean")
        if (
            (career_settings["temperament_enabled"] or career_settings["career_shadow_pass"])
            and not career_settings["career_enabled"]
        ):
            return self._blocked("career modifiers require the career layer")
        career_by_reviewer = {
            reviewer.reviewer_id: None for reviewer in selected
        }
        if career_settings["career_enabled"]:
            try:
                career_assignment = assign_careers(
                    self.catalog, pipeline,
                    (
                        ReviewerCall(
                            reviewer.reviewer_id,
                            reviewer.obligations,
                            reviewer.obligations,
                        )
                        for reviewer in selected
                    ),
                    career_qualifications,
                    purpose=str(career_settings["purpose"]),
                    career_enabled=True,
                    candidate_canary_authorized=career_settings["candidate_canary_authorized"],
                    now_epoch=career_settings["now_epoch"],
                    model_route=str(career_settings["model_route"]),
                    workload_class=str(career_settings["workload_class"]),
                    layer_configuration=str(career_settings["layer_configuration"]),
                )
            except (CareerAssignmentError, TypeError, ValueError):
                return self._blocked("career assignment failed closed validation")
            career_by_reviewer = career_assignment.by_reviewer
        try:
            tracker = ReviewBudgetTracker(limits, allow_parallel=parallel_active)
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
            serialized_packets = {}
            prompt_hashes = {}
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
                if not is_admitted_report_only_transport(provider.transport) or provider.invoke is not None:
                    return self._blocked("selected reviewer has no owned bounded transport")
                provider.transport.validate()
                capability = self.catalog.capability_for(reviewer.reviewer_id)
                if set(capability.readable_files) != set(contents) or capability.allowed_commands or capability.allowed_tests:
                    return self._blocked("reviewer capability does not match the report-only scope")
                skill_bytes = Path(reviewer.skill_path).read_bytes()
                if hashlib.sha256(skill_bytes).hexdigest() != reviewer.skill_sha256:
                    return self._blocked("reviewer skill changed during preflight")
                career_id = career_by_reviewer.get(reviewer.reviewer_id)
                skill = review_prompt(
                    reviewer.reviewer_id, reviewer.lens, self.finding_schema,
                    proof_verifier.descriptions() if proof_verifier else (),
                    career=(self.catalog.careers[career_id] if career_id else None),
                    career_enabled=career_id is not None,
                    temperament_enabled=(
                        career_id is not None and career_settings["temperament_enabled"]
                    ),
                    shadow_enabled=(
                        career_id is not None and career_settings["career_shadow_pass"]
                    ),
                )
                prompt_hashes[reviewer.reviewer_id] = hashlib.sha256(
                    skill.encode("utf-8")
                ).hexdigest()
                from .context import bind_review_context
                skill = bind_review_context(skill, review_context)
                serialized_packets[reviewer.reviewer_id] = provider.transport.payload(
                    skill, contents, limits["max_output_tokens"])
                prepared[reviewer.reviewer_id] = (provider, capability, skill)
            serialized_packets = finalize_packets(serialized_packets, limits)
            packet_plan = admit_packets(serialized_packets,
                max_request_bytes=limits["max_context_bytes"],
                max_input_units=limits["max_estimated_input_tokens"], max_calls=limits["max_model_calls"])
            if approved_packets is not None and packet_plan["packet_sha256"] != approved_packets:
                return self._blocked("approved exception packet changed before dispatch")
            tracker.admitted_packets = tuple(serialized_packets.values())
            tracker.remaining()
            stage = plan_review_stage(
                pipeline=pipeline,
                selected_members=tuple(reviewer.reviewer_id for reviewer in selected),
                parallel_policy=parallel_policy,
                max_model_calls=limits["max_model_calls"],
            )
        except PacketRefused as exc:
            detail = (f"packet admission: requests={exc.report['request_count']} "
                      f"input_units={exc.report['estimated_input_units']} "
                      f"input_limit={exc.report['limits']['aggregate_input_units']}")
            return RunResult(Verdict.INCONCLUSIVE, (), None, (str(exc), detail), (), packet_plan=exc.report)
        except (OSError, ValueError, TypeError, KeyError, ReviewSchedulingError):
            return self._blocked("reviewer preflight failed")
        outcomes: list[ReviewOutcome] = []
        invoked: list[str] = []
        def invoke(reviewer_id):
            provider, capability, skill = prepared[reviewer_id]
            adapter = ReviewerAdapter(capability, contents=contents, source_hashes=source_hashes, hash_provider=hash_provider)
            return adapter.run(provider, budget=tracker, skill=skill)
        try:
            stage_results = run_review_stage(stage, invoke)
        except (ReviewSchedulingError, RuntimeError, ValueError, OSError):
            tracker.failed = True
            stage_results = ()
        for reviewer_id, outcome in stage_results:
            invoked.append(reviewer_id)
            outcomes.append(outcome)
        # Loaded catalogs carry the digest of their complete canonical source,
        # including lens/career versions and every binding. Hand-built test
        # catalogs fall back to a semantic projection instead of hashing IDs.
        catalog_fingerprint = self.catalog.source_sha256 or hashlib.sha256(
            repr((
                sorted(self.catalog.reviewers.items()),
                sorted(self.catalog.careers.items()),
                sorted(self.catalog.lens_matrix.items()),
                sorted(self.catalog.compatibility.items()),
                sorted(self.catalog.desired_distinct_careers.items()),
            )).encode()
        ).hexdigest()
        activation_binding = parallel_policy.get("activation_binding_sha256")
        if activation_binding is not None and (
            not isinstance(activation_binding, str)
            or len(activation_binding) != 64
            or any(value not in "0123456789abcdef" for value in activation_binding)
        ):
            tracker.failed = True
            return RunResult(
                Verdict.INCONCLUSIVE, tuple(outcomes), None,
                ("adaptive activation binding is invalid",), tuple(invoked),
                tracker.public(), packet_plan=packet_plan,
            )
        bound_grants = {
            member: (
                hashlib.sha256(json.dumps({
                    "scheduler_grant_sha256": stage.grant_sha256s[member],
                    "activation_binding_sha256": activation_binding,
                }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if activation_binding is not None
                else stage.grant_sha256s[member]
            )
            for member in stage.selected_members
        }
        def provenance(
            accepted_findings: tuple[dict, ...] = (),
            *, incomplete: bool = False,
        ) -> dict[str, object]:
            by_reviewer: dict[str, list[dict]] = {}
            for finding in accepted_findings:
                by_reviewer.setdefault(finding["reviewer_id"], []).append(finding)
            shadow_results = {}
            for reviewer_id in stage.selected_members:
                enabled = bool(
                    career_by_reviewer.get(reviewer_id)
                    and career_settings["career_shadow_pass"]
                )
                reviewer_findings = by_reviewer.get(reviewer_id, ())[:3]
                finding_ids = sorted({
                    finding["finding_id"] for finding in reviewer_findings
                })[:3]
                checks = sorted({
                    evidence[6:]
                    for finding in reviewer_findings
                    for evidence in finding.get("evidence", ())
                    if isinstance(evidence, str) and evidence.startswith("check:")
                })[:3]
                shadow_results[reviewer_id] = {
                    "status": (
                        "inconclusive" if enabled and incomplete
                        else "completed" if enabled
                        else "not_run"
                    ),
                    # The current finding schema has no explicit Shadow result
                    # channel. Preserve that uncertainty rather than inferring
                    # accepted/rejected from the presence of a finding.
                    "disposition": "unresolved" if enabled else "disabled",
                    "finding_ids": finding_ids,
                    "check_ids": checks,
                }
            return ParallelReviewProvenance(
                scheduler_source_sha256=hashlib.sha256(
                    Path(__file__).with_name("review_scheduler.py").read_bytes()
                ).hexdigest(),
                catalog_sha256=catalog_fingerprint,
                selected_members=stage.selected_members,
                lens_bindings={
                    reviewer.reviewer_id: (reviewer.lens,) for reviewer in selected
                },
                career_assignments={
                    reviewer.reviewer_id: career_by_reviewer.get(reviewer.reviewer_id)
                    for reviewer in selected
                },
                grant_sha256s=bound_grants,
                prompts_sha256=prompt_hashes,
                budget={**limits, "max_concurrent_reviewers": stage.concurrency},
                desired_count=len(stage.selected_members),
                admitted_count=len(stage.selected_members) if stage.batches else 0,
                observed_count=len(invoked),
                frozen_triggers=tuple(sorted(set(triggers))),
                career_shadow_results=shadow_results,
            ).to_dict()
        parallel_provenance = provenance(incomplete=True)
        if any(outcome.verdict is Verdict.INCONCLUSIVE for outcome in outcomes):
            return RunResult(Verdict.INCONCLUSIVE, tuple(outcomes), None,
                ("reviewer execution incomplete",), tuple(invoked), tracker.public(),
                packet_plan=packet_plan, parallel_review=parallel_provenance)
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
                tuple(proof_verifier.receipts) if proof_verifier else (), packet_plan,
                parallel_provenance)
        reconciliation = Reconciliation(
            tuple(f for part in parts for f in part.findings),
            tuple(f for part in parts for f in part.rejected),
            tuple(error for part in parts for error in part.errors))
        parallel_provenance = provenance(
            reconciliation.findings,
            incomplete=bool(
                reconciliation.errors
                or len(invoked) != len(stage.selected_members)
                or not tracker.complete
                or tracker.failed
            ),
        )
        if (len(outcomes) != len(selected) or not required_ids.issubset(invoked)
                or not tracker.complete or tracker.failed
                or any(item.verdict is Verdict.INCONCLUSIVE for item in outcomes) or reconciliation.errors):
            verdict = Verdict.INCONCLUSIVE
        elif reconciliation.findings:
            verdict = Verdict.FINDINGS
        else:
            verdict = Verdict.PASS
        return RunResult(verdict, tuple(outcomes), reconciliation, (), tuple(invoked), tracker.public(),
                         tuple(proof_verifier.receipts) if proof_verifier else (), packet_plan,
                         parallel_provenance)

    @staticmethod
    def _blocked(reason: str) -> RunResult:
        return RunResult(Verdict.INCONCLUSIVE, (), None, (reason,), ())
