#!/usr/bin/env python3
"""Standalone provider-free verification for portable adaptive v2."""

from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


MODULE = Path(__file__).resolve().parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


portable = _load_module("portable_adaptive_v2", MODULE / "adaptive_v2.py")


def _normalized(value):
    return json.loads(json.dumps(value, sort_keys=True))


def _fixtures():
    return [
        {"name": "inactive", "facts": {"ready_count": 4, "running_count": 0, "host_capacity": 8, "parent_reserve": 1, "reviewer_reserve": 1, "verified_streak": 3, "adverse_streak": 0, "backlog_count": 8, "cooldown_until": None, "trusted_feedback_sha256": None, "lineage_sha256": "0" * 64, "candidate_canary_authorized": False}},
        {"name": "host-reserve", "facts": {"ready_count": 4, "running_count": 0, "host_capacity": 4, "parent_reserve": 1, "reviewer_reserve": 1, "verified_streak": 3, "adverse_streak": 0, "backlog_count": 8, "cooldown_until": None, "trusted_feedback_sha256": "0" * 64, "lineage_sha256": "0" * 64, "candidate_canary_authorized": False}},
        {"name": "adverse-cooldown", "facts": {"ready_count": 8, "running_count": 0, "host_capacity": 8, "parent_reserve": 1, "reviewer_reserve": 0, "verified_streak": 3, "adverse_streak": 1, "backlog_count": 8, "cooldown_until": "2030-01-01T00:05:00Z", "trusted_feedback_sha256": "1" * 64, "lineage_sha256": "0" * 64, "candidate_canary_authorized": False}},
    ]


def _packet(root: Path, node: str = "a", **overrides):
    value = {
        "schema_version": "2.0", "packet_id": f"packet-{node}", "node_id": node,
        "workspace_root": os.path.realpath(root), "read_set": [f"src/{node}.in"],
        "write_set": [f"src/{node}.py"], "resource_set": [f"resource:{node}"],
        "resource_state": "complete", "dependencies": [], "base_revision": "b" * 64,
        "expected_artifacts": [f"out/{node}.json"], "assertions": ["tests pass"],
        "budget": {"provider_calls": 1, "tokens": 1000, "wall_seconds": 30, "cost": 0},
        "route_reference": "route-1", "goal_reference": "goal-1",
    }
    value.update(overrides)
    return value


def source_parity(source_workspace: Path) -> list[str]:
    failures = []
    manifest = portable.load_json(MODULE / "source-projection.json")
    repositories = {
        "governed-executor-runtime": source_workspace / "governed-executor-runtime",
        "hermes-agent-self-evolution": source_workspace / "hermes-agent-self-evolution",
    }
    for item in manifest.get("projection", []):
        path = repositories.get(item.get("source_repository"), Path("__missing__")) / item.get("source_path", "")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("source_sha256"):
            failures.append(f"source projection drift: {item.get('source_repository')}/{item.get('source_path')}")
    runtime_policy = portable.load_json(repositories["hermes-agent-self-evolution"] / "config/adaptive-parallel-policy.json")
    packaged_policy = portable.load_json(MODULE / "adaptive-v2.policy.json")
    runtime_comparable = dict(runtime_policy)
    runtime_comparable["$schema"] = packaged_policy["$schema"]
    if runtime_comparable != packaged_policy:
        failures.append("packaged adaptive policy differs from canonical runtime policy")
    runtime_catalog = portable.load_json(repositories["hermes-agent-self-evolution"] / "config/adaptive-reviewer-catalog.json")
    packaged_catalog = portable.load_json(MODULE / "adaptive-v2.catalog.json")
    if runtime_catalog != packaged_catalog:
        failures.append("packaged reviewer/career catalog differs from canonical runtime catalog")
    if failures:
        return failures

    execution = _load_module("canonical_adaptive_parallel", repositories["governed-executor-runtime"] / "src/lapart_executor/adaptive_parallel.py")
    canonical_policy = execution.AdaptiveParallelPolicy.from_mapping(runtime_policy)
    enabled_policy = copy.deepcopy(packaged_policy)
    enabled_policy["activation_state"] = "production"
    enabled_policy["execution"]["enabled"] = True
    enabled_policy["execution"]["qualification_state"] = "production_qualified"
    enabled_policy["execution"]["qualified_ceiling"] = 8
    enabled_policy["grants"]["execution_parallelism"].update(
        enabled=True, state="production_qualified", qualification_reference="a" * 64, expiry="2030-01-01T00:00:00Z",
    )
    runtime_enabled = copy.deepcopy(runtime_policy)
    runtime_enabled["activation_state"] = "production"
    runtime_enabled["execution"].update(enabled=True, qualification_state="production_qualified", qualified_ceiling=8)
    runtime_enabled["grants"]["execution_parallelism"].update(
        enabled=True, state="production_qualified", qualification_reference="a" * 64, expiry="2030-01-01T00:00:00Z",
    )
    canonical_enabled = execution.AdaptiveParallelPolicy.from_mapping(runtime_enabled)
    now = datetime(2029, 1, 1, tzinfo=timezone.utc)
    for fixture in _fixtures():
        selected_portable = packaged_policy if fixture["name"] == "inactive" else enabled_policy
        selected_canonical = canonical_policy if fixture["name"] == "inactive" else canonical_enabled
        expected = execution.decide_admission(selected_canonical, execution.AdaptiveAdmissionFacts.from_mapping(fixture["facts"]), layer="execution", now=now).to_dict()
        actual = portable.decide_admission(selected_portable, fixture["facts"], layer="execution", now=now)
        if actual != expected:
            failures.append(f"execution decision parity failed: {fixture['name']}")

    for strategy, streak, expected_count in (
        ("serial", 99, 1), ("fixed_two", 99, 2),
        ("adaptive", 0, 2), ("adaptive", 3, 4), ("adaptive", 6, 8),
    ):
        portable_policy = copy.deepcopy(enabled_policy)
        canonical_mapping = copy.deepcopy(runtime_enabled)
        portable_policy["strategy"] = canonical_mapping["strategy"] = strategy
        case_facts = _fixtures()[1]["facts"] | {
            "host_capacity": 12, "parent_reserve": 0, "reviewer_reserve": 0,
            "ready_count": 9, "backlog_count": 9, "verified_streak": streak,
        }
        expected = execution.decide_admission(
            execution.AdaptiveParallelPolicy.from_mapping(canonical_mapping),
            execution.AdaptiveAdmissionFacts.from_mapping(case_facts),
            layer="execution", now=now,
        ).to_dict()
        actual = portable.decide_admission(portable_policy, case_facts, layer="execution", now=now)
        if actual != expected or actual["admitted_count"] != expected_count:
            failures.append(f"strategy/tier parity failed: {strategy}/{streak}")

    shadow_portable = copy.deepcopy(enabled_policy)
    shadow_runtime = copy.deepcopy(runtime_enabled)
    shadow_portable["activation_state"] = shadow_runtime["activation_state"] = "shadow"
    shadow_facts = _fixtures()[1]["facts"] | {"host_capacity": 8}
    expected = execution.decide_admission(
        execution.AdaptiveParallelPolicy.from_mapping(shadow_runtime),
        execution.AdaptiveAdmissionFacts.from_mapping(shadow_facts),
        layer="execution", now=now,
    ).to_dict()
    actual = portable.decide_admission(shadow_portable, shadow_facts, layer="execution", now=now)
    if actual != expected or actual["admitted_count"] != 0 or "SHADOW_OBSERVATION_ONLY" not in actual["limiting_reasons"]:
        failures.append("shadow observation-only parity failed")

    rollout_cases = (
        ("canary-denied", "canary", "candidate", False, "0", 0),
        ("canary-selected", "canary", "candidate", True, "0", 2),
        ("canary-not-selected", "canary", "candidate", True, "f", 0),
        ("pilot-limited-selected", "pilot_limited", "canary_qualified", False, "0", 2),
        ("pilot-limited-not-selected", "pilot_limited", "canary_qualified", False, "8", 0),
        ("pilot-wider-selected", "pilot_wider", "canary_qualified", False, "7", 2),
        ("pilot-wider-not-selected", "pilot_wider", "canary_qualified", False, "8", 0),
    )
    for name, activation, grant_state, authorized, lineage_prefix, expected_count in rollout_cases:
        portable_policy = copy.deepcopy(enabled_policy)
        canonical_mapping = copy.deepcopy(runtime_enabled)
        portable_policy["activation_state"] = canonical_mapping["activation_state"] = activation
        portable_policy["grants"]["execution_parallelism"]["state"] = grant_state
        canonical_mapping["grants"]["execution_parallelism"]["state"] = grant_state
        case_facts = _fixtures()[1]["facts"] | {
            "candidate_canary_authorized": authorized,
            "lineage_sha256": lineage_prefix * 64,
            "host_capacity": 8, "verified_streak": 0,
        }
        expected = execution.decide_admission(
            execution.AdaptiveParallelPolicy.from_mapping(canonical_mapping),
            execution.AdaptiveAdmissionFacts.from_mapping(case_facts),
            layer="execution", now=now,
        ).to_dict()
        actual = portable.decide_admission(portable_policy, case_facts, layer="execution", now=now)
        if actual != expected or actual["admitted_count"] != expected_count:
            failures.append(f"rollout parity failed: {name}")

    root = source_workspace / "governed-executor-runtime"
    packet_cases = (
        ("independent", _packet(root), _packet(root, "b"), False),
        ("case", _packet(root, write_set=["SRC/Output.py"]), _packet(root, "b", read_set=["src/output.PY"]), True),
        ("directory", _packet(root, write_set=["src/package"]), _packet(root, "b", read_set=["src/package/module.py"]), True),
        ("resource", _packet(root), _packet(root, "b", resource_set=["resource:a"]), True),
        ("unknown-resource", _packet(root, resource_set=[], resource_state="unknown"), _packet(root, "b"), True),
        ("nested-root", _packet(root), _packet(root / "tests", "b"), True),
    )
    for name, left, right, expected_conflict in packet_cases:
        canonical_conflict = execution.packets_conflict(
            execution.AdaptiveWorkPacket.from_mapping(left),
            execution.AdaptiveWorkPacket.from_mapping(right),
        )
        portable_conflict = portable.packets_conflict(left, right)
        if canonical_conflict != portable_conflict or portable_conflict is not expected_conflict:
            failures.append(f"packet conflict parity failed: {name}")

    canonical_realpath = execution.os.path.realpath
    portable_realpath = portable.os.path.realpath
    def resolve_alias(value):
        return canonical_realpath(str(value).replace("\\", "/").replace("/linked-src/", "/real-src/"))
    try:
        execution.os.path.realpath = resolve_alias
        portable.os.path.realpath = resolve_alias
        left = _packet(root, write_set=["linked-src/output.py"])
        right = _packet(root, "b", read_set=["real-src/output.py"])
        if not execution.packets_conflict(
            execution.AdaptiveWorkPacket.from_mapping(left),
            execution.AdaptiveWorkPacket.from_mapping(right),
        ) or not portable.packets_conflict(left, right):
            failures.append("packet conflict parity failed: symlink-alias")
    finally:
        execution.os.path.realpath = canonical_realpath
        portable.os.path.realpath = portable_realpath

    sys.path.insert(0, str(repositories["hermes-agent-self-evolution"]))
    try:
        from prime_bridge.adaptive_review import careers as runtime_careers
        from prime_bridge.adaptive_review import catalog as runtime_catalog_module
        from prime_bridge.adaptive_review.models import Pipeline
        from prime_bridge.adaptive_review.review_scheduler import plan_review_stage as runtime_plan_review_stage
        runtime_loaded_catalog = runtime_catalog_module.load_frozen_catalog(
            repositories["hermes-agent-self-evolution"] / "config/adaptive-reviewer-catalog.json",
            vault_root=source_workspace,
            readable_files=(),
        )
        review_template = portable.load_json(MODULE / "adaptive-v2.review.json")
        for activation in ("inactive", "shadow", "canary", "pilot_limited", "pilot_wider", "review", "production"):
            template = copy.deepcopy(review_template)
            template["activation_state"] = activation
            policy = {"activation_state": activation, "qualification_verified": True, "lineage_sha256": "0" * 64, "initial_ceiling": template["initial_ceiling"], "max_concurrent_reviewers": template["max_concurrent_reviewers"]}
            for pipeline, members in (("S0", ()), ("S2", ("a", "b")), ("S3", ("a", "b", "c", "d"))):
                expected = runtime_plan_review_stage(pipeline=Pipeline(pipeline), selected_members=members, parallel_policy=policy, max_model_calls=template["max_model_calls"][pipeline], host_slots=4, execution_slots=1).to_dict()
                actual = portable.plan_review_stage(template, pipeline=pipeline, selected_members=members, max_model_calls=template["max_model_calls"][pipeline], host_slots=4, execution_slots=1, qualification_verified=True, lineage_sha256="0" * 64)
                if actual != expected:
                    failures.append(f"review decision parity failed: {activation}/{pipeline}")
        for activation in ("canary", "review", "production"):
            template = copy.deepcopy(review_template)
            template["activation_state"] = activation
            policy = {"activation_state": activation, "qualification_verified": False, "lineage_sha256": "0" * 64, "initial_ceiling": template["initial_ceiling"], "max_concurrent_reviewers": template["max_concurrent_reviewers"]}
            members = ("a", "b", "c", "d")
            expected = runtime_plan_review_stage(pipeline=Pipeline.S3, selected_members=members, parallel_policy=policy, max_model_calls=5).to_dict()
            actual = portable.plan_review_stage(template, pipeline="S3", selected_members=members, max_model_calls=5, qualification_verified=False, lineage_sha256="0" * 64)
            if actual != expected:
                failures.append(f"review qualification authority parity failed: {activation}")
        for activation in ("pilot_limited", "pilot_wider"):
            template = copy.deepcopy(review_template)
            template["activation_state"] = activation
            for lineage in ("0" * 64, "f" * 64, None):
                policy = {"activation_state": activation, "qualification_verified": True, "lineage_sha256": lineage, "initial_ceiling": template["initial_ceiling"], "max_concurrent_reviewers": template["max_concurrent_reviewers"]}
                members = ("a", "b", "c", "d")
                expected = runtime_plan_review_stage(pipeline=Pipeline.S3, selected_members=members, parallel_policy=policy, max_model_calls=5).to_dict()
                actual = portable.plan_review_stage(template, pipeline="S3", selected_members=members, max_model_calls=5, qualification_verified=True, lineage_sha256=lineage)
                if actual != expected:
                    failures.append(f"review pilot lineage parity failed: {activation}/{lineage or 'missing'}")
        calls = [
            {"reviewer_id": "behavior", "obligations": ["behavioral_correctness"]},
            {"reviewer_id": "evidence", "obligations": ["evidence_integrity"]},
            {"reviewer_id": "architecture", "obligations": ["architecture_ownership"]},
        ]
        qualifications = [
            {"reviewer_id": "behavior", "career_id": "engineer", "covered_obligations": ["behavioral_correctness"], "state": "production_qualified"},
            {"reviewer_id": "evidence", "career_id": "anthropologist", "covered_obligations": ["evidence_integrity"], "state": "production_qualified"},
            {"reviewer_id": "architecture", "career_id": "structuralist", "covered_obligations": ["architecture_ownership"], "state": "production_qualified"},
        ]
        runtime_calls = [runtime_careers.ReviewerCall(item["reviewer_id"], tuple(item["obligations"])) for item in calls]
        runtime_qualifications = [runtime_careers.CareerQualification(item["reviewer_id"], item["career_id"], tuple(item["covered_obligations"]), item["state"]) for item in qualifications]
        expected = _normalized(asdict(runtime_careers.assign_careers(runtime_loaded_catalog, "S3", runtime_calls, runtime_qualifications)))
        actual = _normalized(portable.assign_careers(packaged_catalog, pipeline="S3", calls=calls, qualifications=qualifications))
        if actual != expected:
            failures.append("career assignment parity failed")
    finally:
        sys.path.pop(0)
    return failures


def verify(source_workspace: Path | None = None) -> list[str]:
    failures = []
    policy = portable.load_json(MODULE / "adaptive-v2.policy.json")
    review = portable.load_json(MODULE / "adaptive-v2.review.json")
    catalog = portable.load_json(MODULE / "adaptive-v2.catalog.json")
    try:
        portable.validate_policy(policy, shipped=True)
        portable.validate_review_template(review, shipped=True)
        portable.validate_catalog(catalog)
    except portable.AdaptiveV2Error as exc:
        return [str(exc)]
    now = datetime(2029, 1, 1, tzinfo=timezone.utc)
    inactive = portable.decide_admission(policy, _fixtures()[0]["facts"], layer="execution", now=now)
    if inactive["admitted_count"] != 0 or inactive["limiting_reasons"] != ["INACTIVE"]:
        failures.append("fresh v2 did not remain inactive")
    for name, mutate in (
        ("bool-as-int", lambda value: value["execution"].update(initial_ceiling=True)),
        ("invalid-enum", lambda value: value.update(activation_state="automatic")),
        ("extra-field", lambda value: value.update(hidden_authority=True)),
    ):
        candidate = copy.deepcopy(policy)
        mutate(candidate)
        try:
            portable.validate_policy(candidate, shipped=True)
            failures.append(f"named failing input was accepted: {name}")
        except portable.AdaptiveV2Error:
            pass
    if review["provider_calls_for_selection"] != 0 or any(item["enabled"] for item in review["grants"].values()):
        failures.append("shipped grants or selection calls are active")
    if portable.plan_review_stage(review, pipeline="S0", selected_members=(), max_model_calls=0)["concurrency"] != 0:
        failures.append("S0 acquired a reviewer")
    serial = portable.plan_review_stage(review, pipeline="S3", selected_members=("ponytail-review", "evidence-integrity-audit", "improve-codebase-architecture"), max_model_calls=5)
    if serial["concurrency"] != 1 or serial["selected_members"] != ["ponytail-review", "evidence-integrity-audit", "improve-codebase-architecture"]:
        failures.append("inactive review changed required membership or serial compatibility")
    neutral = portable.assign_careers(catalog, pipeline="S3", calls=[{"reviewer_id": "a", "obligations": ["behavioral_correctness"]}], qualifications=[])
    if neutral["actual_distinct_count"] != 0 or neutral["model_calls_added"] != 0:
        failures.append("unqualified career overlay did not stay neutral")
    calls = [
        {"reviewer_id": "a", "obligations": ["behavioral_correctness"]},
        {"reviewer_id": "b", "obligations": ["behavioral_correctness"]},
    ]
    equal_method = [
        {"reviewer_id": "a", "career_id": "game_theorist", "covered_obligations": ["behavioral_correctness"], "state": "production_qualified"},
        {"reviewer_id": "b", "career_id": "psychoanalyst", "covered_obligations": ["behavioral_correctness"], "state": "production_qualified"},
    ]
    result = portable.assign_careers(catalog, pipeline="S2", calls=calls, qualifications=equal_method)
    if result["actual_distinct_count"] > 1 or portable.method_distance(catalog, "game_theorist", "psychoanalyst") != 0:
        failures.append("equal-method careers counted as distinct")
    reordered = copy.deepcopy(catalog)
    reordered["careers"].reverse()
    if portable.assign_careers(reordered, pipeline="S2", calls=calls, qualifications=equal_method) != result:
        failures.append("career selection changed after catalog reordering")
    adapter = portable.load_json(MODULE / "adaptive-v2.adapter.example.json")
    if any(adapter["capabilities"].values()) or any(value is not False for value in adapter["authority"].values()) or any(value is not None for value in adapter["ports"].values()):
        failures.append("shipped adapter template is active or authoritative")
    selector_source = (MODULE / "prime_bridge/adaptive_execution_provider.py").read_text(encoding="utf-8")
    batch_source = (MODULE / "prime_bridge/orca_adaptive_batch.py").read_text(encoding="utf-8")
    execution_source = (MODULE / "prime_bridge/orca_execution.py").read_text(encoding="utf-8")
    operator_source = (MODULE / "prime_bridge/orca_operator.py").read_text(encoding="utf-8")
    qualifier_source = (MODULE / "scripts/qualify-orca-coding.py").read_text(encoding="utf-8")
    production_contract = (
        "adapter_launch", "ORCA_ADAPTER_QUALIFICATION_MISMATCH",
        "ELIGIBLE_ORCA_LANES", "automatic_dispatch_authorized",
        "until_material_change", "ORCA_MATERIAL_FINGERPRINT_MISMATCH",
    )
    if any(fragment not in selector_source for fragment in production_contract):
        failures.append("adaptive Orca selector contract is incomplete")
    if ("Start every admitted worker before waiting" not in batch_source
            or "preauthorize(request_id, argument_hash)" not in batch_source
            or "aggregate_verification" not in batch_source
            or "rollback_complete" not in batch_source):
        failures.append("adaptive Orca batch contract is incomplete")
    if (not (MODULE / "prime_bridge/git_interop.py").is_file()
            or "git_argv(worktree" not in execution_source
            or "PYTHONDONTWRITEBYTECODE=1" not in execution_source):
        failures.append("portable Hermes Orca worktree interop is incomplete")
    if ('"--orca", default=None' not in operator_source):
        failures.append("Orca operator does not default to governed discovery")
    if ("ORCA_AB_QUALIFICATION_EXPIRED" in selector_source
            or "timedelta(days=7)" in qualifier_source):
        failures.append("portable Orca qualification still has calendar expiry")
    if ("--harness" not in qualifier_source
            or "--adapter-launch" not in qualifier_source
            or "records[args.harness] = record" not in qualifier_source
            or "_qualification_records" not in qualifier_source
            or "--migrate-persistent" not in qualifier_source
            or "--promote-registry" not in qualifier_source):
        failures.append("real coding A/B qualifier contract is incomplete")
    orca_adapters = portable.load_json(MODULE / "config/orca-harness-adapters.json")
    expected_adapter_contract = {
        "codex": ("native", "codex", ()),
        "hermes": ("terminal", None, ("hermes", "--profile", "workspace")),
        "claude-code": ("native", "claude", ()),
        "moa": ("terminal", None, ("moa",)),
        "cursor": ("native", "cursor", ()),
        "dsh": ("terminal", None, ("dsh",)),
        "pi": ("terminal", None, ("pi",)),
        "opencode": ("terminal", None, ("opencode",)),
        "antigravity": ("terminal", None, ("antigravity",)),
    }
    actual_adapter_contract = {
        name: (row.get("launch"), row.get("agent"), tuple(row.get("command", ())))
        for name, row in orca_adapters.get("adapters", {}).items()
    }
    if (actual_adapter_contract != expected_adapter_contract
            or any(row.get("qualification") != "configured"
                   for row in orca_adapters.get("adapters", {}).values())):
        failures.append("portable Orca adapters contain activation or host identity")
    inventory = portable.load_json(MODULE / "source-hashes.json")
    for relative, expected in inventory.get("files", {}).items():
        path = MODULE.parents[1] / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            failures.append(f"packaged source hash mismatch: {relative}")
    projection = portable.load_json(MODULE / "source-projection.json")
    if len(projection.get("projection", [])) < 7 or any(len(item.get("source_sha256", "")) != 64 for item in projection.get("projection", [])):
        failures.append("source projection manifest is incomplete")
    if source_workspace is not None:
        failures.extend(source_parity(source_workspace.resolve()))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-workspace", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    failures = verify(args.source_workspace)
    result = {"status": "PASS" if not failures else "HOLD", "provider_calls": 0, "provider_tokens": 0, "activation_changes": 0, "failures": failures}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif failures:
        print("HOLD: adaptive v2 portable verification")
        for failure in failures:
            print(f"- {failure}")
    else:
        print("PASS: inactive adaptive v2 execution, reviewer, and career contracts")
        print("PASS: provider_calls=0, provider_tokens=0, activation_changes=0")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
