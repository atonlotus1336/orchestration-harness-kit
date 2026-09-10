from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


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
