"""Contracts for the unified orchestration control surface."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from prime_bridge.orchestration_switch import (
    FEATURES,
    HARNESS_REGISTRY,
    OrchestrationSwitch,
    SwitchControlError,
    effective_mode,
    load_mode,
    operation_permitted,
)

ROOT = Path(__file__).resolve().parents[2]


def test_missing_state_preserves_every_existing_owner(tmp_path):
    owner = OrchestrationSwitch(tmp_path)
    status = owner.status()
    assert status["global"] == "ACTIVE"
    assert status["source"] == "legacy_default"
    assert set(status["features"]) == set(FEATURES)
    assert status["features"]["orca"]["harnesses"] == {
        harness: "observe" for harness in HARNESS_REGISTRY
    }
    assert all(
        row["harnesses"] == {harness: "on" for harness in HARNESS_REGISTRY}
        for name, row in status["features"].items()
        if name not in {"orca", "herdr-review"}
    )
    assert status["features"]["herdr-review"]["harnesses"] == {
        harness: "off" for harness in HARNESS_REGISTRY
    }


def test_all_full_materializes_explicit_state(tmp_path):
    owner = OrchestrationSwitch(tmp_path)
    result = owner.set("all", "full", HARNESS_REGISTRY)
    assert result["changed"] is True
    assert result["result"] == "ACTIVE"
    assert len(result["changed_cells"]) == len(FEATURES) * len(HARNESS_REGISTRY)
    assert owner.status()["source"] == "explicit"
    assert all(load_mode(name, "codex", owner.path) == "full" for name in FEATURES)


def test_feature_off_is_harness_specific_and_idempotent(tmp_path):
    owner = OrchestrationSwitch(tmp_path)
    first = owner.set("review", "off", ("codex",))
    assert first["after"]["features"]["review"]["global"] == "PARTIAL"
    assert load_mode("review", "codex", owner.path) == "off"
    assert load_mode("review", "hermes", owner.path) == "on"
    prior = owner.path.read_bytes()
    second = owner.set("review", "off", ("codex",))
    assert second["changed"] is False
    assert owner.path.read_bytes() == prior


def test_component_specific_modes_are_closed(tmp_path):
    owner = OrchestrationSwitch(tmp_path)
    assert owner.set("route", "prime-omp", HARNESS_REGISTRY)["result"] == "ACTIVE"
    assert owner.set("token-compute", "adaptive", HARNESS_REGISTRY)["result"] == "ACTIVE"
    with pytest.raises(SwitchControlError):
        owner.set("memory", "prime-omp", HARNESS_REGISTRY)
    with pytest.raises(SwitchControlError):
        owner.set("all", "adaptive", HARNESS_REGISTRY)


def test_full_resolves_to_maximum_capability_without_bypassing_controls(tmp_path):
    owner = OrchestrationSwitch(tmp_path)
    owner.set("all", "full", HARNESS_REGISTRY)
    assert effective_mode("route", "codex", owner.path) == "prime-omp"
    assert effective_mode("memory", "hermes", owner.path) == "read-write"
    assert operation_permitted("learning", "codex", "promote", owner.path)
    assert operation_permitted("rag", "hermes", "index", owner.path)
    assert operation_permitted("token-compute", "codex", "budget", owner.path)
    assert not operation_permitted("review", "codex", "launch", owner.path)


def test_restricted_modes_limit_operations(tmp_path):
    owner = OrchestrationSwitch(tmp_path)
    owner.set("memory", "recall", ("codex",))
    owner.set("learning", "observe", ("codex",))
    owner.set("route", "native-only", ("codex",))
    assert operation_permitted("memory", "codex", "recall", owner.path)
    assert not operation_permitted("memory", "codex", "write", owner.path)
    assert operation_permitted("learning", "codex", "record", owner.path)
    assert not operation_permitted("learning", "codex", "promote", owner.path)
    assert operation_permitted("route", "codex", "native", owner.path)
    assert not operation_permitted("route", "codex", "prime", owner.path)


def test_failed_verification_restores_exact_prior_bytes(tmp_path, monkeypatch):
    owner = OrchestrationSwitch(tmp_path)
    owner.set("all", "on", HARNESS_REGISTRY)
    prior = owner.path.read_bytes()

    def fail(_expected):
        raise SwitchControlError("synthetic")

    monkeypatch.setattr(owner, "_verify", fail)
    result = owner.set("rag", "full", ("hermes",))
    assert result["result"] == "FAILED_ROLLED_BACK"
    assert result["changed"] is False
    assert owner.path.read_bytes() == prior


def test_failed_receipt_restores_exact_prior_bytes(tmp_path, monkeypatch):
    owner = OrchestrationSwitch(tmp_path)
    owner.set("all", "on", HARNESS_REGISTRY)
    prior = owner.path.read_bytes()

    def fail(*_args):
        raise OSError("synthetic")

    monkeypatch.setattr(owner, "_receipt", fail)
    result = owner.set("rag", "full", ("hermes",))
    assert result["result"] == "FAILED_ROLLED_BACK"
    assert result["changed"] is False
    assert owner.path.read_bytes() == prior


def test_invalid_state_fails_closed(tmp_path):
    path = tmp_path / "orchestration-switches.json"
    path.write_text('{"schema_version":"1.0","harnesses":{}}\n')
    owner = OrchestrationSwitch(tmp_path)
    assert owner.status()["global"] == "INVALID"
    with pytest.raises(SwitchControlError):
        load_mode("route", "codex", path)


def test_cli_supports_simple_global_and_harness_commands(tmp_path):
    script = ROOT / "scripts" / "orchestration-control.py"
    base = [sys.executable, str(script), "--state-root", str(tmp_path), "--json"]
    full = subprocess.run([*base, "all", "full", "--global"],
                          text=True, capture_output=True, check=False)
    assert full.returncode == 0, full.stdout + full.stderr
    assert json.loads(full.stdout)["result"] == "ACTIVE"
    off = subprocess.run([*base, "memory", "off", "--harness", "codex"],
                         text=True, capture_output=True, check=False)
    assert off.returncode == 0, off.stdout + off.stderr
    status = subprocess.run([*base, "status", "--feature", "memory"],
                            text=True, capture_output=True, check=False)
    assert status.returncode == 0
    value = json.loads(status.stdout)
    assert value["features"]["memory"]["global"] == "PARTIAL"


def test_route_off_preflight_selects_native_without_omp_probe(tmp_path):
    owner = OrchestrationSwitch(tmp_path)
    owner.set("route", "off", ("codex",))
    script = ROOT / "scripts" / "prime-code-execute.py"
    environment = dict(os.environ)
    environment["LAPART_ORCHESTRATION_SWITCH_STATE"] = str(owner.path)
    result = subprocess.run([
        sys.executable, str(script), "--workspace", str(ROOT.parent),
        "--project", str(ROOT), "--harness", "codex", "--preflight-only",
    ], text=True, capture_output=True, check=False, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout.strip().splitlines()[-1])
    assert value["status"] == "READY"
    assert value["mode"] == "native-only"
    assert value["prime_required"] is False
    assert value["omp_required"] is False
    assert "omp_probe" not in value


def test_review_off_skips_evaluation_cleanly(tmp_path):
    owner = OrchestrationSwitch(tmp_path)
    owner.set("review", "off", ("codex",))
    environment = dict(os.environ)
    environment["LAPART_ORCHESTRATION_SWITCH_STATE"] = str(owner.path)
    script = ROOT / "scripts" / "adaptive-code-review.py"
    result = subprocess.run([
        sys.executable, str(script), "evaluate", "--harness", "codex",
        "--scope", "checkpoint", "--path", "README.md",
    ], text=True, capture_output=True, check=False, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["state"] == "disabled"
    assert value["reason"] == "REVIEW_SWITCH_OFF"
