from __future__ import annotations

import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "modules/additive-parallel"
CONFORMANCE = ROOT / "modules/conformance-kit/verify-additive-parallel.py"


def load_feature():
    spec = importlib.util.spec_from_file_location("additive_parallel", MODULE / "verify-additive-parallel.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_shipped_profile_is_inactive_and_provider_free():
    feature = load_feature()
    profile = json.loads((MODULE / "additive-parallel.profile.json").read_text(encoding="utf-8"))
    feature.validate_profile(profile, shipped=True)
    assert profile["feature_state"] == profile["core_activation_state"] == "inactive"
    assert feature.verify() == []


def test_mutating_orca_and_parallel_limit_profiles_fail_closed():
    feature = load_feature()
    profile = json.loads((MODULE / "additive-parallel.profile.json").read_text(encoding="utf-8"))
    for mutate in (
        lambda value: value["limits"].update(max_concurrent_children=3),
        lambda value: value["prohibited_capabilities"].update(file_writes=True),
        lambda value: value["orca"].update(may_complete_canonical_job=True),
    ):
        candidate = json.loads(json.dumps(profile))
        mutate(candidate)
        try:
            feature.validate_profile(candidate, shipped=True)
        except feature.ProfileError:
            continue
        raise AssertionError("unsafe profile was accepted")


def test_feature_conformance_is_provider_free():
    process = subprocess.run([sys.executable, str(CONFORMANCE)], cwd=ROOT, text=True, capture_output=True, check=False)
    assert process.returncode == 0, process.stdout + process.stderr
    assert "provider_calls=0" in process.stdout


def test_module_manifest_registers_optional_inactive_feature():
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entry = next(row for row in manifest["modules"] if row["id"] == "additive-parallel-observation")
    assert entry["required"] is False
    assert entry["source"] == "modules/additive-parallel"


def test_adaptive_v2_is_inactive_and_matches_canonical_source():
    verifier = MODULE / "verify-adaptive-v2.py"
    result = subprocess.run(
        [sys.executable, str(verifier), "--source-workspace", str(ROOT.parent), "--json"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt == {"activation_changes": 0, "failures": [], "provider_calls": 0, "provider_tokens": 0, "status": "PASS"}


def test_adaptive_v2_stage_and_rollback_preserve_local_files(tmp_path):
    manager_path = MODULE / "manage-adaptive-v2.py"
    spec = importlib.util.spec_from_file_location("adaptive_manager", manager_path)
    manager = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(manager)
    target, backups = tmp_path / "module", tmp_path / "data" / "backups"
    target.mkdir(parents=True)
    local = target / "recipient-activation.json"
    local.write_text('{"state":"review"}\n', encoding="utf-8")
    prior = target / "README.md"
    prior.write_bytes(b"recipient prior bytes\n")
    preview = manager.stage(target, backups, apply=False, yes=False)
    assert preview["status"] == "DRY-RUN" and prior.read_bytes() == b"recipient prior bytes\n"
    staged = manager.stage(target, backups, apply=True, yes=True)
    assert staged["status"] == "PASS" and staged["feature_state"] == "inactive"
    assert local.read_text(encoding="utf-8") == '{"state":"review"}\n'
    restored = manager.rollback(target, backups, staged["snapshot"], apply=True, yes=True)
    assert restored["status"] == "PASS"
    assert prior.read_bytes() == b"recipient prior bytes\n"
    assert local.read_text(encoding="utf-8") == '{"state":"review"}\n'


def test_adaptive_v2_rejects_authority_and_equal_method_diversity():
    spec = importlib.util.spec_from_file_location("adaptive_contract", MODULE / "adaptive_v2.py")
    adaptive = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(adaptive)
    catalog = adaptive.load_json(MODULE / "adaptive-v2.catalog.json")
    assert adaptive.method_distance(catalog, "game_theorist", "psychoanalyst") == 0
    adapter = adaptive.load_json(MODULE / "adaptive-v2.adapter.example.json")
    assert all(value is False for value in adapter["authority"].values())


def _load_adaptive_v2():
    spec = importlib.util.spec_from_file_location("adaptive_contract_final", MODULE / "adaptive_v2.py")
    adaptive = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(adaptive)
    return adaptive


def _facts(**overrides):
    value = {
        "ready_count": 9, "running_count": 0, "host_capacity": 12,
        "parent_reserve": 0, "reviewer_reserve": 0, "verified_streak": 0,
        "adverse_streak": 0, "backlog_count": 9, "cooldown_until": None,
        "trusted_feedback_sha256": "0" * 64, "lineage_sha256": "0" * 64,
        "candidate_canary_authorized": False,
    }
    value.update(overrides)
    return value


def _packet(root, node="a", **overrides):
    value = {
        "schema_version": "2.0", "packet_id": f"packet-{node}", "node_id": node,
        "workspace_root": os.path.realpath(root), "read_set": [f"src/{node}.in"],
        "write_set": [f"src/{node}.py"], "resource_set": [f"resource:{node}"],
        "resource_state": "complete", "dependencies": [], "base_revision": "b" * 64,
        "expected_artifacts": [f"out/{node}.json"], "assertions": ["tests pass"],
        "budget": {"provider_calls": 0, "tokens": 0, "wall_seconds": 30, "cost": 0},
        "route_reference": "route-1", "goal_reference": "goal-1",
    }
    value.update(overrides)
    return value


def _enabled_policy(adaptive, *, activation="production", strategy="adaptive", grant_state="production_qualified"):
    policy = adaptive.load_json(MODULE / "adaptive-v2.policy.json")
    policy["activation_state"] = activation
    policy["strategy"] = strategy
    policy["execution"].update(enabled=True, qualification_state=grant_state, qualified_ceiling=8)
    policy["grants"]["execution_parallelism"].update(
        enabled=True, state=grant_state, qualification_reference="a" * 64, expiry="2030-01-01T00:00:00Z",
    )
    return policy


def test_adaptive_v2_strategy_tiers_shadow_and_rollout_authority():
    adaptive = _load_adaptive_v2()
    now = datetime(2029, 1, 1, tzinfo=timezone.utc)
    for strategy, streak, expected in (
        ("serial", 99, 1), ("fixed_two", 99, 2),
        ("adaptive", 0, 2), ("adaptive", 3, 4), ("adaptive", 6, 8),
    ):
        result = adaptive.decide_admission(
            _enabled_policy(adaptive, strategy=strategy), _facts(verified_streak=streak),
            layer="execution", now=now,
        )
        assert result["desired_count"] == result["admitted_count"] == expected
    shadow = adaptive.decide_admission(
        _enabled_policy(adaptive, activation="shadow"), _facts(verified_streak=3),
        layer="execution", now=now,
    )
    assert shadow["desired_count"] == 2 and shadow["admitted_count"] == 0
    assert "SHADOW_OBSERVATION_ONLY" in shadow["limiting_reasons"]
    denied = adaptive.decide_admission(
        _enabled_policy(adaptive, activation="canary", grant_state="candidate"),
        _facts(), layer="execution", now=now,
    )
    assert denied["limiting_reasons"] == ["CANDIDATE_CANARY_UNAUTHORIZED"]
    selected = adaptive.decide_admission(
        _enabled_policy(adaptive, activation="canary", grant_state="candidate"),
        _facts(candidate_canary_authorized=True, lineage_sha256="0" * 64),
        layer="execution", now=now,
    )
    assert selected["admitted_count"] == 2
    not_selected = adaptive.decide_admission(
        _enabled_policy(adaptive, activation="pilot_wider", grant_state="canary_qualified"),
        _facts(lineage_sha256="8" * 64), layer="execution", now=now,
    )
    assert not_selected["admitted_count"] == 0
    assert "PILOT_NOT_SELECTED" in not_selected["limiting_reasons"]


def test_adaptive_v2_five_grants_and_dependencies_fail_closed():
    adaptive = _load_adaptive_v2()
    policy = adaptive.load_json(MODULE / "adaptive-v2.policy.json")
    missing = copy.deepcopy(policy)
    missing["grants"].pop("temperament")
    with pytest.raises(adaptive.AdaptiveV2Error, match="exactly the five"):
        adaptive.validate_policy(missing)
    schema = adaptive.load_json(MODULE / "adaptive-v2-policy.schema.json")
    assert schema["definitions"]["grant"]["allOf"][0]["then"]["properties"]["expiry"]["type"] == "string"
    no_expiry = _enabled_policy(adaptive)
    no_expiry["grants"]["execution_parallelism"]["expiry"] = None
    with pytest.raises(adaptive.AdaptiveV2Error, match="requires an expiry"):
        adaptive.validate_policy(no_expiry)
    modifier = copy.deepcopy(policy)
    modifier["grants"]["temperament"].update(
        enabled=True, state="canary_qualified", qualification_reference="d" * 64, expiry="2030-01-01T00:00:00Z",
    )
    with pytest.raises(adaptive.AdaptiveV2Error, match="career_kernel"):
        adaptive.validate_policy(modifier)
    career = copy.deepcopy(policy)
    career["grants"]["career_kernel"].update(
        enabled=True, state="canary_qualified", qualification_reference="e" * 64, expiry="2030-01-01T00:00:00Z",
    )
    with pytest.raises(adaptive.AdaptiveV2Error, match="reviewer_concurrency"):
        adaptive.validate_policy(career)


def test_adaptive_v2_review_stage_requires_qualification_and_stable_pilot_lineage():
    adaptive = _load_adaptive_v2()
    review = adaptive.load_json(MODULE / "adaptive-v2.review.json")
    review["activation_state"] = "production"
    members = ("r1", "r2", "r3", "r4")
    assert adaptive.plan_review_stage(
        review, pipeline="S3", selected_members=members, max_model_calls=5,
    )["concurrency"] == 1
    assert adaptive.plan_review_stage(
        review, pipeline="S3", selected_members=members, max_model_calls=5,
        qualification_verified=True,
    )["concurrency"] == 4
    for state, selected_width in (("pilot_limited", 2), ("pilot_wider", 4)):
        review["activation_state"] = state
        assert adaptive.plan_review_stage(
            review, pipeline="S3", selected_members=members, max_model_calls=5,
            qualification_verified=True, lineage_sha256="0" * 64,
        )["concurrency"] == selected_width
        assert adaptive.plan_review_stage(
            review, pipeline="S3", selected_members=members, max_model_calls=5,
            qualification_verified=True, lineage_sha256="f" * 64,
        )["concurrency"] == 1


def test_adaptive_v2_packet_conflicts_cover_case_directory_root_resource_and_alias(tmp_path, monkeypatch):
    adaptive = _load_adaptive_v2()
    nested = tmp_path / "nested"
    nested.mkdir()
    assert not adaptive.packets_conflict(_packet(tmp_path), _packet(tmp_path, "b"))
    assert adaptive.packets_conflict(
        _packet(tmp_path, write_set=["SRC/Output.py"]),
        _packet(tmp_path, "b", read_set=["src/output.PY"]),
    )
    assert adaptive.packets_conflict(
        _packet(tmp_path, write_set=["src/package"]),
        _packet(tmp_path, "b", read_set=["src/package/module.py"]),
    )
    assert adaptive.packets_conflict(_packet(tmp_path), _packet(tmp_path, "b", resource_set=["resource:a"]))
    assert adaptive.packets_conflict(
        _packet(tmp_path, resource_set=[], resource_state="unknown"), _packet(tmp_path, "b"),
    )
    assert adaptive.packets_conflict(_packet(tmp_path), _packet(nested, "b"))
    realpath = adaptive.os.path.realpath
    monkeypatch.setattr(
        adaptive.os.path, "realpath",
        lambda value: realpath(str(value).replace("\\", "/").replace("/linked-src/", "/real-src/")),
    )
    assert adaptive.packets_conflict(
        _packet(tmp_path, write_set=["linked-src/output.py"]),
        _packet(tmp_path, "b", read_set=["real-src/output.py"]),
    )