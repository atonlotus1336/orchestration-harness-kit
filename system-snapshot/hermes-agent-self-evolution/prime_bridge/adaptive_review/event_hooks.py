"""Deterministic post-evaluation hooks shared by every harness.

The hooks classify evidence, emit a renderer-neutral architecture delta, and
prepare bounded preventive-learning candidates.  They never call a model,
start a service, promote memory, or grant execution/review authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .models import Pipeline


PREVENTIVE_RULES = {
    "scope_contract": (
        "orchestration.declared-scope",
        "Keep implementation changes within the declared path set; expand the existing task authorization before editing outside that scope.",
    ),
    "behavior_regression": (
        "orchestration.regression-proof",
        "For a bug fix, reproduce the failure before the change and pass the same bounded check afterward, or record an approved exemption with alternative verification.",
    ),
    "verification_mutation": (
        "orchestration.nonmutating-verification",
        "Verification commands must not modify project files; treat any verification mutation as failed execution evidence.",
    ),
}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _normalized_paths(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({PurePosixPath(value).as_posix() for value in values if value}))


def _subsystem(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if not parts or parts[0] in {"tests", "test", "docs", ".planning"}:
        return None
    if parts[0] in {"apps", "packages", "services", "src"} and len(parts) > 1:
        return "/".join(parts[:2])
    return parts[0]


def _architecture_reasons(
    pipeline: Pipeline,
    paths: Sequence[str],
    config: Mapping[str, Any],
) -> tuple[str, ...]:
    reasons: set[str] = set()
    if pipeline.value in config["always_for"]:
        reasons.add(f"pipeline:{pipeline.value}")
    tokens = {item.casefold() for item in config["structural_path_tokens"]}
    for path in paths:
        parts = {part.casefold() for part in PurePosixPath(path).parts}
        name = PurePosixPath(path).name.casefold()
        if name in tokens or parts & tokens:
            reasons.add(f"structural-path:{path}")
    subsystems = {item for path in paths if (item := _subsystem(path)) is not None}
    if len(subsystems) >= config["cross_subsystem_minimum"]:
        reasons.add("cross-subsystem:" + ",".join(sorted(subsystems)))
    return tuple(sorted(reasons))


def _preventive_candidates(
    findings: Iterable[Mapping[str, Any]],
    verification_mutations: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    evidence_by_category: dict[str, set[str]] = {}
    finding_ids: dict[str, set[str]] = {}
    for finding in findings:
        category = finding.get("category")
        if category not in PREVENTIVE_RULES:
            continue
        finding_ids.setdefault(category, set()).add(str(finding.get("finding_id", "unknown")))
        evidence_by_category.setdefault(category, set()).update(
            str(item) for item in finding.get("evidence", ()) if item
        )
    if verification_mutations:
        category = "verification_mutation"
        finding_ids.setdefault(category, set()).add("automatic:verification-mutation")
        evidence_by_category.setdefault(category, set()).update(verification_mutations)

    result = []
    for category in sorted(evidence_by_category):
        subject, content = PREVENTIVE_RULES[category]
        evidence = sorted(evidence_by_category[category])
        result.append({
            "subject": subject,
            "lane": "procedure",
            "target_scope": "global",
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "dedupe_key": _digest({"subject": subject, "content": content}),
            "finding_ids": sorted(finding_ids[category]),
            "evidence": evidence,
            "prevention_test": True,
            "promotion_owner": "existing_learning_pipeline",
        })
    return tuple(result)


def parallel_feedback_from_review(
    result: Mapping[str, Any], *, source_identity: str,
    observed_identity: str, execution_evidence_status: str,
) -> dict[str, Any] | None:
    """Project one owner-produced review receipt into scheduler feedback.

    The projection uses only Controller/Runner output and host-observed source
    identity. Missing parallel provenance is intentionally not promoted.
    """
    parallel = result.get("parallel_review")
    if not isinstance(parallel, Mapping):
        return None
    selected = tuple(parallel.get("selected_members", ()))
    observed = parallel.get("observed_count")
    budget = parallel.get("budget")
    if (
        not selected or type(observed) is not int
        or not isinstance(budget, Mapping)
    ):
        return None
    findings = tuple(
        item for item in result.get("findings", ()) if isinstance(item, Mapping)
    )
    signatures = [
        _digest({
            "category": item.get("category"), "title": item.get("title"),
            "evidence": sorted(item.get("evidence", ())),
        })
        for item in findings
    ]
    duplicate_count = len(signatures) - len(set(signatures))
    lens_bindings = parallel.get("lens_bindings", {})
    careers = parallel.get("career_assignments", {})
    shadow = parallel.get("career_shadow_results", {})
    lens_value: dict[str, float] = {}
    career_value: dict[str, int] = {}
    for finding in findings:
        reviewer_id = finding.get("reviewer_id")
        for lens in lens_bindings.get(reviewer_id, ()):
            lens_value[str(lens)] = lens_value.get(str(lens), 0.0) + 1.0
        career = careers.get(reviewer_id)
        if isinstance(career, str):
            career_value[career] = career_value.get(career, 0) + 1
    dispositions = {
        reviewer_id: str(item["disposition"])
        for reviewer_id, item in shadow.items()
        if isinstance(item, Mapping)
        and item.get("disposition") in {"accepted", "rejected", "unresolved"}
    }
    unresolved = sum(value == "unresolved" for value in dispositions.values())
    capacity = budget.get("max_concurrent_reviewers", 0)
    if type(capacity) is not int or capacity < 0:
        capacity = 0
    complete = result.get("review_completed") is True
    received = min(max(observed, 0), len(selected))
    return {
        "source_identity": source_identity,
        "observed_identity": observed_identity,
        "integrated_verification_status": (
            "VERIFIED" if execution_evidence_status == "VERIFIED"
            and result.get("review_verdict") == "PASS" else "INCONCLUSIVE"
        ),
        "review_complete": complete,
        "expected_reports": len(selected),
        "received_reports": received,
        "schema_valid": True,
        "budget_complete": complete and result.get("model_calls") is not None,
        "review_backlog": max(0, len(selected) - received),
        "review_capacity": capacity,
        "worker_conflicts": 0 if execution_evidence_status == "VERIFIED" else 1,
        "duplicate_rate": duplicate_count / max(1, len(signatures)),
        "correlation_rate": duplicate_count / max(1, len(signatures)),
        "unresolved_disagreements": unresolved,
        "lens_marginal_value": lens_value,
        "career_unique_findings": career_value,
        "shadow_dispositions": dispositions,
        "career_method_overlap": {
            career: 0.0 for career in sorted(career_value)
        },
    }


def evaluate_event_hooks(
    *,
    pipeline: Pipeline,
    declared_paths: Iterable[str],
    actual_changed_paths: Iterable[str] = (),
    before_identity: str | None = None,
    after_identity: str | None = None,
    current_identity: str | None = None,
    evidence_status: str = "UNVERIFIED",
    findings: Iterable[Mapping[str, Any]] = (),
    verification_mutations: Iterable[str] = (),
    s4_confirmed: bool = False,
    s4_grant=None,
    policy: Mapping[str, Any],
    parallel_feedback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return closed, inline hook artifacts for one completed evaluation."""
    config = policy["evaluation_hooks"]
    declared = _normalized_paths(declared_paths)
    actual = _normalized_paths(actual_changed_paths)
    mutations = _normalized_paths(verification_mutations)
    report_paths = actual or declared

    if after_identity is None or current_identity is None:
        freshness = "UNVERIFIED"
    elif after_identity == current_identity:
        freshness = "CURRENT"
    else:
        freshness = "STALE"
    observed = bool(actual and before_identity and after_identity)
    evidence_integrity = {
        "status": evidence_status,
        "freshness": freshness,
        "requires_inconclusive": freshness == "STALE",
        "epistemic_labels": {
            "change_contract": "trusted_instruction",
            "git_changes": "direct_observation" if observed else "unverified",
            "executed_checks": "direct_observation" if evidence_status != "UNVERIFIED" else "unverified",
            "review_selection": "deterministic_inference",
        },
        "source_identity": after_identity,
        "observed_identity": current_identity,
    }

    reasons = _architecture_reasons(pipeline, report_paths, config["architecture_delta"])
    automatic = False
    if s4_grant is not None:
        from .policy import validate_s4_grant
        automatic = validate_s4_grant(s4_grant, source_identity=after_identity)
    if pipeline is Pipeline.S4 and not (s4_confirmed or automatic):
        architecture = {
            "status": "DEFERRED_CONFIRMATION",
            "triggered": False,
            "reasons": list(reasons or ("pipeline:S4",)),
            "report": None,
        }
    elif reasons:
        architecture = {
            "status": "EMITTED",
            "triggered": True,
            "reasons": list(reasons),
            "report": {
                "schema_version": "1.0",
                "kind": "architecture_delta",
                "base_identity": before_identity,
                "head_or_snapshot_identity": after_identity,
                "changed_paths": list(report_paths),
                "source_refs": [
                    {
                        "path": path,
                        "evidence_class": "direct_observation" if actual else "trusted_instruction",
                    }
                    for path in report_paths
                ],
                "renderer_contract": "provider-independent-json",
            },
        }
    else:
        architecture = {"status": "SKIPPED", "triggered": False, "reasons": [], "report": None}

    candidates = _preventive_candidates(findings, mutations)
    learning = {
        "status": "CANDIDATE_READY" if candidates else "NO_CANDIDATE",
        "candidates": list(candidates),
        "promotion_performed": False,
        "promotion_owner": "existing_learning_pipeline",
        "null_output_allowed": True,
    }
    adaptive_feedback = None
    if parallel_feedback is not None:
        from .parallel_feedback import adaptive_feedback_receipt
        adaptive_feedback = adaptive_feedback_receipt(parallel_feedback)
    return {
        "schema_version": "1.0",
        "automatic": config["automatic"],
        "model_calls": 0,
        "evidence_integrity": evidence_integrity,
        "architecture_delta": architecture,
        "learning": learning,
        "adaptive_parallel_feedback": adaptive_feedback,
    }
