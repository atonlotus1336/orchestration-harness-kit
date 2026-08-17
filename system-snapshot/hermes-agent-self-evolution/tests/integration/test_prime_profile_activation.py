import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts/verify-prime-profile-readiness.py"


def run_verify(tmp_path, config, receipt, mode="prime-default"):
    config_path = tmp_path / "config.yaml"
    receipt_path = tmp_path / "prime-overlay-receipt.json"
    config_path.write_text(yaml.safe_dump(config))
    receipt_path.write_text(json.dumps(receipt))
    return subprocess.run([sys.executable, str(VERIFY), "--config", str(config_path), "--receipt", str(receipt_path), "--profile", "workspace", "--mode", mode], capture_output=True, text=True)


def test_eligible_profile_requires_managed_overlay(tmp_path):
    receipt = {"profile": "workspace", "mode": "prime-default", "prime_available": True}
    config = {"providers": {"ollama": {}}, "mcp_servers": {"prime-agent": {"command": "bash"}}, "memory": {}, "toolsets": {}}
    result = run_verify(tmp_path, config, receipt)
    assert result.returncode == 0 and json.loads(result.stdout)["prime_ready"] is True


def test_native_only_rejects_prime_overlay(tmp_path):
    receipt = {"profile": "workspace", "mode": "native-only", "prime_available": False}
    result = run_verify(tmp_path, {"mcp_servers": {"prime-agent": {}}}, receipt, mode="native-only")
    assert result.returncode == 1
