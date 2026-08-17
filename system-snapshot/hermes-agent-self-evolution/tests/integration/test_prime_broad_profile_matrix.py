import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def create_profile(root: Path, name: str):
    profile = root / name
    profile.mkdir()
    config = {
        "model": {"provider": "custom:ollama-cloud-local", "default": "deepseek-v4-flash:cloud"},
        "providers": {"ollama-cloud-local": {}}, "mcp_servers": {"memory": {"command": "python"}},
        "memory": {"enabled": True}, "toolsets": ["hermes-cli"],
    }
    (profile / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts/render-prime-profile-overlay.py"),
        "--profiles-root", str(root), "--profile", name,
        "--policy", str(ROOT / "config/prime-profile-policy.json"),
        "--ledger", str(root / "jobs.sqlite3"),
    ], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def audit(root: Path, rollout: Path):
    return subprocess.run([
        sys.executable, str(ROOT / "scripts/audit-prime-profile-overlays.py"),
        "--profiles-root", str(root), "--policy", str(ROOT / "config/prime-profile-policy.json"),
        "--rollout-policy", str(rollout),
    ], cwd=ROOT, text=True, capture_output=True)


def test_nonvacuous_broad_matrix_meets_eligible_coverage(tmp_path):
    for name in ("workspace", "home", "research", "coding", "telegram"):
        create_profile(tmp_path, name)
    result = audit(tmp_path, ROOT / "config/prime-rollout-policy.json")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["rollout"] == {
        "stage": "broad", "eligible_profiles": 5, "eligible_available": 5,
        "coverage_percent": 100, "minimum_percent": 80,
        "excluded_profiles": [],
    }
    assert value["native_only"] == []


def test_empty_or_insufficient_matrix_fails(tmp_path):
    empty = audit(tmp_path, ROOT / "config/prime-rollout-policy.json")
    assert empty.returncode != 0 and "no staged" in empty.stderr
    for name in ("home", "research", "coding", "telegram"):
        create_profile(tmp_path, name)
    policy = json.loads((ROOT / "config/prime-rollout-policy.json").read_text(encoding="utf-8"))
    policy["stage"] = "canary"
    path = tmp_path / "rollout.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    result = audit(tmp_path, path)
    assert result.returncode != 0 and "coverage" in result.stderr
