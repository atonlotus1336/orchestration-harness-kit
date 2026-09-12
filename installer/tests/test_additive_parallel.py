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
    assert {"adaptive-switch.py", "adaptive_switch.py"} <= set(manager.MANAGED_FILES)
    for switch_file in ("adaptive-switch.py", "adaptive_switch.py"):
        assert (target / switch_file).read_bytes() == (MODULE / switch_file).read_bytes()
    assert local.read_text(encoding="utf-8") == '{"state":"review"}\n'
    prior.write_bytes(b"recipient upgrade bytes\n")
    upgraded = manager.stage(target, backups, apply=True, yes=True)
    assert upgraded["status"] == "PASS" and prior.read_bytes() == (MODULE / "README.md").read_bytes()
    assert manager.rollback(
        target, backups, upgraded["snapshot"], apply=True, yes=True,
    )["status"] == "PASS"
    assert prior.read_bytes() == b"recipient upgrade bytes\n"
    restored = manager.rollback(target, backups, staged["snapshot"], apply=True, yes=True)
    assert restored["status"] == "PASS"
    assert prior.read_bytes() == b"recipient prior bytes\n"
    assert local.read_text(encoding="utf-8") == '{"state":"review"}\n'
    uninstall_backups = tmp_path / "data" / "uninstall-backups"
    restaged = manager.stage(
        target, tmp_path / "data" / "restage-backups", apply=True, yes=True)
    assert restaged["status"] == "PASS"
    assert manager.uninstall(
        target, uninstall_backups, apply=False, yes=False)["status"] == "DRY-RUN"
    removed = manager.uninstall(
        target, uninstall_backups, apply=True, yes=True)
    assert removed["status"] == "PASS"
    assert not any((target / name).exists() for name in manager.MANAGED_FILES)
    assert local.read_text(encoding="utf-8") == '{"state":"review"}\n'
    assert manager.rollback(
        target, uninstall_backups, removed["snapshot"], apply=True, yes=True,
    )["status"] == "PASS"
    assert all((target / name).is_file() for name in manager.MANAGED_FILES)


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


def _load_portable_switch():
    spec = importlib.util.spec_from_file_location(
        "portable_switch", MODULE / "adaptive_switch.py")
    switch = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(switch)
    return switch


class _TransactionalAdapter:
    def __init__(self):
        self.active = False
        self.transactions = {}
        self.fail_prepare = False
        self.fail_commit = False
        self.fail_rollback = False
        self.fail_finalize = False
        self.committed_states = []

    def prepare(self, transaction_id, desired_state):
        self.transactions[transaction_id] = (self.active, desired_state)
        if self.fail_prepare:
            raise RuntimeError("prepare failed")
        return {"state": "PREPARED"}

    def commit(self, transaction_id):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        _, desired_state = self.transactions[transaction_id]
        self.active = desired_state == "ACTIVE"
        self.committed_states.append(desired_state)
        return {"state": "COMMITTED"}

    def rollback(self, transaction_id):
        if self.fail_rollback:
            raise RuntimeError("rollback failed")
        if transaction_id in self.transactions:
            self.active = self.transactions[transaction_id][0]
        return {"state": "ROLLED_BACK"}

    def finalize(self, transaction_id):
        if self.fail_finalize:
            raise RuntimeError("finalize failed")
        self.transactions.pop(transaction_id, None)
        return {"state": "FINALIZED"}

    def verify(self):
        return {"state": "ACTIVE" if self.active else "INACTIVE"}

    def refresh(self):
        return {"state": "REFRESHED"}


def test_portable_switch_global_subset_and_actual_deactivation(tmp_path):
    switch = _load_portable_switch()
    adapters = {name: _TransactionalAdapter() for name in ("codex", "hermes")}
    owner = switch.PortableSwitch(tmp_path, tuple(adapters), adapters)
    assert owner.status()["global"] == "INACTIVE"
    assert owner.set(("codex",), turn_on=True)["after"]["global"] == "PARTIAL"
    assert owner.set(("hermes",), turn_on=True)["after"]["global"] == "ACTIVE"
    result = owner.set(("codex",), turn_on=False)
    assert result["after"]["global"] == "PARTIAL"
    assert adapters["codex"].active is False
    assert adapters["codex"].committed_states[-1] == "INACTIVE"
    assert owner.set(("hermes",), turn_on=False)["after"]["global"] == "INACTIVE"
    assert adapters["hermes"].active is False


@pytest.mark.parametrize("turn_on", [True, False])
def test_portable_switch_compensates_partial_activation_and_deactivation(
        tmp_path, turn_on):
    switch = _load_portable_switch()
    adapters = {name: _TransactionalAdapter() for name in ("codex", "hermes")}
    owner = switch.PortableSwitch(tmp_path, tuple(adapters), adapters)
    if not turn_on:
        assert owner.set(tuple(adapters), turn_on=True)["result"] == "ACTIVE"
    expected = turn_on is False
    prior_bytes = owner.path.read_bytes() if owner.path.exists() else None
    adapters["hermes"].fail_commit = True
    failed = owner.set(tuple(adapters), turn_on=turn_on)
    assert failed["result"] == "FAILED_ROLLED_BACK"
    assert adapters["codex"].active is expected
    assert adapters["hermes"].active is expected
    assert (owner.path.read_bytes() if owner.path.exists() else None) == prior_bytes
    assert failed["after"]["global"] == ("ACTIVE" if expected else "INACTIVE")


def test_portable_switch_compensates_paper_write_failure(tmp_path, monkeypatch):
    switch = _load_portable_switch()
    adapters = {name: _TransactionalAdapter() for name in ("codex", "hermes")}
    owner = switch.PortableSwitch(tmp_path, tuple(adapters), adapters)
    assert owner.set(("hermes",), turn_on=True)["result"] == "PARTIAL"
    prior_bytes = owner.path.read_bytes()

    def fail_write(_value):
        raise OSError("disk full")

    monkeypatch.setattr(owner, "_write", fail_write)
    failed = owner.set(("codex",), turn_on=True)
    assert failed["result"] == "FAILED_ROLLED_BACK"
    assert adapters["codex"].active is False
    assert adapters["hermes"].active is True
    assert owner.path.read_bytes() == prior_bytes


def test_portable_switch_detects_unrestored_paper_state(tmp_path, monkeypatch):
    switch = _load_portable_switch()
    adapters = {name: _TransactionalAdapter() for name in ("codex", "hermes")}
    owner = switch.PortableSwitch(tmp_path, tuple(adapters), adapters)
    assert owner.set(("hermes",), turn_on=True)["result"] == "PARTIAL"
    adapters["codex"].fail_commit = True

    def corrupt_restore(_prior_bytes):
        owner.path.write_bytes(b"corrupt")

    monkeypatch.setattr(owner, "_restore", corrupt_restore)
    failed = owner.set(("codex",), turn_on=True)
    assert failed["result"] == "ROLLBACK_INCOMPLETE"
    assert {"harness": "paper_state", "phase": "verify_restoration"} in failed["rollback_errors"]


def test_portable_switch_reports_incomplete_rollback_and_invalid_state(tmp_path):
    switch = _load_portable_switch()
    adapters = {name: _TransactionalAdapter() for name in ("codex", "hermes")}
    owner = switch.PortableSwitch(tmp_path, tuple(adapters), adapters)
    adapters["codex"].fail_rollback = True
    adapters["hermes"].fail_commit = True
    failed = owner.set(tuple(adapters), turn_on=True)
    assert failed["result"] == "ROLLBACK_INCOMPLETE"
    assert failed["after"]["global"] == "INVALID"
    assert adapters["codex"].active is True
    assert not owner.path.exists()


def test_portable_switch_status_verifies_disabled_adapters(tmp_path):
    switch = _load_portable_switch()
    adapters = {name: _TransactionalAdapter() for name in ("codex", "hermes")}
    owner = switch.PortableSwitch(tmp_path, tuple(adapters), adapters)
    adapters["codex"].active = True
    assert owner.status()["global"] == "INVALID"


def test_portable_switch_cli_operates_configured_fake_adapters(tmp_path):
    adapter_module = tmp_path / "recipient_adapters.py"
    adapter_module.write_text(
        """from pathlib import Path
HARNESS_REGISTRY = ("codex", "hermes")

class Adapter:
    def __init__(self, root, name):
        self.path = Path(root) / ("external-" + name)
        self.transactions = {}
    def prepare(self, transaction_id, desired_state):
        self.transactions[transaction_id] = (self.path.exists(), desired_state)
        return {"state": "PREPARED"}
    def commit(self, transaction_id):
        _prior, desired = self.transactions[transaction_id]
        if desired == "ACTIVE":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("active", encoding="utf-8")
        elif self.path.exists():
            self.path.unlink()
        return {"state": "COMMITTED"}
    def rollback(self, transaction_id):
        prior, _desired = self.transactions.get(transaction_id, (False, "INACTIVE"))
        if prior:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("active", encoding="utf-8")
        elif self.path.exists():
            self.path.unlink()
        return {"state": "ROLLED_BACK"}
    def finalize(self, transaction_id):
        self.transactions.pop(transaction_id, None)
        return {"state": "FINALIZED"}
    def verify(self):
        return {"state": "ACTIVE" if self.path.exists() else "INACTIVE"}
    def refresh(self):
        return {"state": "REFRESHED"}

def build_adapters(state_root):
    return {name: Adapter(state_root, name) for name in HARNESS_REGISTRY}
""",
        encoding="utf-8",
    )
    base = [
        sys.executable, str(MODULE / "adaptive-switch.py"),
        "--state-root", str(tmp_path / "state"),
        "--adapter-module", str(adapter_module),
    ]
    activated = subprocess.run(
        [*base, "on", "--global"], cwd=MODULE, text=True,
        capture_output=True, check=False)
    assert activated.returncode == 0, activated.stdout + activated.stderr
    assert json.loads(activated.stdout)["activation_changes"] == 2
    deactivated = subprocess.run(
        [*base, "off", "--harness", "codex"], cwd=MODULE, text=True,
        capture_output=True, check=False)
    assert deactivated.returncode == 0, deactivated.stdout + deactivated.stderr
    receipt = json.loads(deactivated.stdout)
    assert receipt["status"] == "PARTIAL" and receipt["activation_changes"] == 1
    assert not (tmp_path / "state" / "external-codex").exists()
    assert (tmp_path / "state" / "external-hermes").is_file()
    status = subprocess.run(
        [*base, "status"], cwd=MODULE, text=True,
        capture_output=True, check=False)
    assert status.returncode == 0
    assert json.loads(status.stdout)["status"] == "PARTIAL"

def test_portable_switch_cli_missing_adapter_is_nonzero():
    process = subprocess.run(
        [sys.executable, str(MODULE / "adaptive-switch.py"), "on", "--global"],
        cwd=MODULE, text=True, capture_output=True, check=False)
    assert process.returncode == 3
    assert json.loads(process.stdout) == {
        "activation_changes": 0, "command": "on",
        "status": "ADAPTER_REQUIRED", "targets": ["codex", "hermes"]}
    invalid = subprocess.run(
        [sys.executable, str(MODULE / "adaptive-switch.py"), "on"],
        cwd=MODULE, text=True, capture_output=True, check=False)
    assert invalid.returncode == 2


def test_portable_orca_observer_switch_cli_is_simple_and_inactive_by_default(tmp_path):
    base = [
        sys.executable, str(MODULE / "orca-observer.py"),
        "--state-root", str(tmp_path / "state"),
    ]
    initial = subprocess.run(
        [*base, "status"], cwd=MODULE, text=True,
        capture_output=True, check=False)
    assert initial.returncode == 0
    assert json.loads(initial.stdout)["status"] == "INACTIVE"
    active = subprocess.run(
        [*base, "on", "--global"], cwd=MODULE, text=True,
        capture_output=True, check=False)
    assert active.returncode == 0
    assert json.loads(active.stdout)["activation_changes"] == 2
    partial = subprocess.run(
        [*base, "off", "--harness", "codex"], cwd=MODULE, text=True,
        capture_output=True, check=False)
    value = json.loads(partial.stdout)
    assert partial.returncode == 0
    assert value["status"] == "PARTIAL"
    assert value["result"]["after"]["authority"] == {
        "observe_only": True, "launch": False, "command": False,
        "approve": False, "review": False, "complete": False, "poll": False,
    }


def test_portable_unified_switch_is_simple_and_inactive_by_default(tmp_path):
    base = [
        sys.executable, str(MODULE / "orchestration-control.py"),
        "--state-root", str(tmp_path / "state"),
    ]
    initial = subprocess.run(
        [*base, "status"], cwd=MODULE, text=True,
        capture_output=True, check=False)
    assert initial.returncode == 0
    assert json.loads(initial.stdout)["global"] == "INACTIVE"
    active = subprocess.run(
        [*base, "all", "full", "--global"], cwd=MODULE, text=True,
        capture_output=True, check=False)
    assert active.returncode == 0, active.stdout + active.stderr
    assert json.loads(active.stdout)["result"] == "ACTIVE"
    partial = subprocess.run(
        [*base, "memory", "off", "--harness", "codex"], cwd=MODULE,
        text=True, capture_output=True, check=False)
    assert partial.returncode == 0
    status = subprocess.run(
        [*base, "status", "--feature", "memory"], cwd=MODULE,
        text=True, capture_output=True, check=False)
    assert json.loads(status.stdout)["features"]["memory"]["global"] == "PARTIAL"


def test_adaptive_stage_includes_orca_switch_files():
    manager_path = MODULE / "manage-adaptive-v2.py"
    spec = importlib.util.spec_from_file_location("adaptive_manager_orca", manager_path)
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    assert {
        "orca-observer.py", "orca_observer_switch.py",
        "orchestration-control.py", "orchestration_switch.py",
    } <= set(manager.MANAGED_FILES)
