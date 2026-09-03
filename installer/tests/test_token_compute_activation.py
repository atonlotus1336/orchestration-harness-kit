from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "modules/token-compute-optimization"
CLI = MODULE / "activate-token-compute.py"
POLICY = MODULE / "token-compute-policy.json"


def run_cli(*args):
    result = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode, json.loads(result.stdout)


def test_provider_free_all_harness_activation_and_verification(tmp_path):
    registry = tmp_path / "activation.json"
    code, dry = run_cli(
        "activate", "--registry", registry, "--harness", "all",
        "--approval-reference", "test-approval",
    )
    assert code == 0 and dry["status"] == "DRY-RUN" and not registry.exists()

    code, active = run_cli(
        "activate", "--registry", registry, "--harness", "all",
        "--approval-reference", "test-approval", "--apply", "--yes",
    )
    assert code == 0 and active["status"] == "PASS"
    assert active["provider_calls"] == 0 and active["service_restarts"] == 0

    code, verified = run_cli("verify", "--registry", registry, "--require-all-active")
    assert code == 0 and verified["status"] == "PASS"
    assert set(verified["harnesses"]) == {
        "hermes", "opencode", "pi", "antigravity", "codex", "claude-code", "moa", "cursor",
    }
    assert verified["marginal_value_stop"] == "shadow"


def test_activation_update_has_content_addressed_rollback(tmp_path):
    registry = tmp_path / "activation.json"
    code, _ = run_cli(
        "activate", "--registry", registry, "--harness", "cursor",
        "--approval-reference", "first", "--apply", "--yes",
    )
    assert code == 0
    before = registry.read_bytes()
    code, updated = run_cli(
        "activate", "--registry", registry, "--harness", "all",
        "--approval-reference", "second", "--apply", "--yes",
    )
    assert code == 0 and updated["rollback_snapshot"]
    code, rolled_back = run_cli(
        "rollback", "--registry", registry, "--snapshot", updated["rollback_snapshot"],
        "--apply", "--yes",
    )
    assert code == 0 and rolled_back["status"] == "PASS"
    assert registry.read_bytes() == before


def test_policy_hash_refresh_snapshots_safe_existing_registry(tmp_path):
    registry = tmp_path / "activation.json"
    code, _ = run_cli(
        "activate", "--registry", registry, "--harness", "cursor",
        "--approval-reference", "first", "--apply", "--yes",
    )
    assert code == 0
    changed_policy = tmp_path / "policy.json"
    changed_policy.write_text(
        json.dumps(json.loads(POLICY.read_text(encoding="utf-8")), indent=4) + "\n",
        encoding="utf-8",
    )
    code, refreshed = run_cli(
        "--policy", changed_policy, "activate", "--registry", registry,
        "--harness", "all", "--approval-reference", "refresh", "--apply", "--yes",
    )
    assert code == 0 and refreshed["rollback_snapshot"]
    code, verified = run_cli(
        "--policy", changed_policy, "verify", "--registry", registry,
        "--require-all-active",
    )
    assert code == 0 and verified["status"] == "PASS"


def test_policy_rejects_enforced_stop_and_weakened_safety(tmp_path):
    spec = importlib.util.spec_from_file_location("token_activation", MODULE / "lapart_token_compute/activation.py")
    activation = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(activation)
    value = json.loads(POLICY.read_text(encoding="utf-8"))
    value["controls"]["marginal_value_stop"] = "enforce"
    changed = tmp_path / "unsafe.json"
    changed.write_text(json.dumps(value), encoding="utf-8")
    try:
        activation.load_policy(changed)
    except activation.ActivationError:
        pass
    else:
        raise AssertionError("unsafe stop enforcement did not fail closed")


def test_required_module_is_in_default_install_path():
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entry = next(row for row in manifest["modules"] if row["id"] == "token-compute-optimization")
    assert entry["required"] is True
    assert entry["source"] == "modules/token-compute-optimization"
    assert entry["id"] in manifest["default_path"]
