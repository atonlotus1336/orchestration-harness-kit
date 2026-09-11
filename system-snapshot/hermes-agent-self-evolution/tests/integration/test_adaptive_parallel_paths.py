"""Local activation must not contaminate a distributable default."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from prime_bridge import adaptive_parallel_paths as paths
from prime_bridge.adaptive_parallel_activation import ActivationManager, load_policy

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    for name in ("POLICY", "QUALIFICATION", "RECEIPTS"):
        monkeypatch.delenv(f"LAPART_ADAPTIVE_{name}_PATH", raising=False)
    shipped = tmp_path / "config" / "adaptive-parallel-policy.json"
    shipped.parent.mkdir()
    shipped.write_bytes((ROOT / "config/adaptive-parallel-policy.json").read_bytes())
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "SHIPPED_POLICY", shipped)
    monkeypatch.setattr(paths, "LOCAL_STATE", tmp_path / ".state" / "adaptive-parallel")
    return shipped


def test_fresh_install_uses_inactive_default(isolated_paths):
    owner = ActivationManager()
    assert owner.policy_path == isolated_paths
    assert owner.status()["state"] == "inactive"
    assert owner.qualification_path == paths.ROOT / ".planning/adaptive-parallel-qualification.json"


def test_local_state_keeps_default_bytes_and_missing_grants_fail_closed(isolated_paths):
    before = isolated_paths.read_bytes()
    paths.LOCAL_STATE.mkdir(parents=True)
    value = json.loads(before)
    value["activation_state"] = "canary"
    local = paths.LOCAL_STATE / "policy.json"
    local.write_text(json.dumps(value))
    owner = ActivationManager()
    assert owner.policy_path == local
    assert owner.status()["state"] == "canary"
    assert owner.qualification_path == paths.LOCAL_STATE / "qualification.json"
    assert owner.receipts_path == paths.LOCAL_STATE / "receipts"
    # Historical grants must never replace the missing local store.
    legacy = paths.ROOT / ".planning/adaptive-parallel-qualification.json"
    legacy.parent.mkdir()
    legacy.write_text('{"unexpected": "historical grants"}')
    with pytest.raises(ValueError, match="no current"):
        owner.promote(target="canary", layers=("execution",), harness="hermes",
                      profile="workspace", model_route_sha256="a" * 64,
                      workload_class="coding")
    assert isolated_paths.read_bytes() == before


def test_malformed_local_policy_is_not_silently_replaced(isolated_paths):
    paths.LOCAL_STATE.mkdir(parents=True)
    (paths.LOCAL_STATE / "policy.json").write_text("not JSON")
    with pytest.raises(json.JSONDecodeError):
        load_policy(paths.policy_path())


def test_explicit_paths_override_environment_without_creation(isolated_paths, tmp_path, monkeypatch):
    for name, resolver in (("POLICY", paths.policy_path),
                           ("QUALIFICATION", paths.qualification_path),
                           ("RECEIPTS", paths.receipts_path)):
        env_path = tmp_path / (name + "-env")
        explicit = tmp_path / (name + "-explicit")
        monkeypatch.setenv(f"LAPART_ADAPTIVE_{name}_PATH", str(env_path))
        assert resolver() == env_path
        assert resolver(explicit) == explicit
        assert not env_path.exists() and not explicit.exists()


def test_operator_cli_can_inspect_shipped_default_without_touching_local_state():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/adaptive-parallel.py"),
         "--policy", str(ROOT / "config/adaptive-parallel-policy.json"), "status"],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(completed.stdout)["state"] == "inactive"
