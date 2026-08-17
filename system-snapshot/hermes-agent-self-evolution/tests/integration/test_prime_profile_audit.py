from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_profile_audit_accepts_preserved_staged_overlay(tmp_path):
    profile = tmp_path / "home"
    profile.mkdir()
    config = {
        "model": {"provider": "custom:ollama-cloud-local", "default": "deepseek-v4-flash:cloud"},
        "providers": {"ollama-cloud-local": {}}, "mcp_servers": {"memory": {"command": "python"}},
        "memory": {"enabled": True}, "toolsets": ["hermes-cli"],
    }
    (profile / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    render = subprocess.run([
        sys.executable, str(ROOT / "scripts/render-prime-profile-overlay.py"),
        "--profiles-root", str(tmp_path), "--profile", "home",
        "--policy", str(ROOT / "config/prime-profile-policy.json"),
        "--ledger", str(tmp_path / "jobs.sqlite3"),
    ], cwd=ROOT, text=True, capture_output=True)
    assert render.returncode == 0, render.stderr
    audit = subprocess.run([
        sys.executable, str(ROOT / "scripts/audit-prime-profile-overlays.py"),
        "--profiles-root", str(tmp_path),
        "--policy", str(ROOT / "config/prime-profile-policy.json"),
    ], cwd=ROOT, text=True, capture_output=True)
    assert audit.returncode == 0, audit.stderr
    assert '"status": "PASS"' in audit.stdout
