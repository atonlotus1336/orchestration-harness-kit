from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / "scripts" / "capture-prime-integration-baseline.sh"
SCHEMA = json.loads((ROOT / "contracts" / "hermes-baseline-evidence.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(COLLECTOR), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": "/usr/local/bin:/usr/bin:/bin"},
    )


def capture() -> dict:
    completed = run("capture")
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    VALIDATOR.validate(value)
    return value


def test_live_collector_binds_exact_health_listener_authority_and_sections() -> None:
    value = capture()
    health = value["health"]
    assert health["url"] == "http://127.0.0.1:8642/health"
    if health["status"] == "READY":
        body = base64.b64decode(health["body_base64"])
        parsed = json.loads(body)
        assert set(parsed) == {"status", "platform", "version"}
        assert parsed["status"] == "ok"
        assert parsed["platform"] == "hermes-agent"
        assert isinstance(parsed["version"], str) and parsed["version"]
    assert value["bindings"]["gateway_8642"]["expected"] == "0.0.0.0"
    assert value["bindings"]["dashboard_9119"]["expected"] == "127.0.0.1-or-not-listening"
    if value["listener_identity"].get("pid"):
        assert value["listener_identity"]["uid"] == os.getuid()
        assert value["listener_identity"]["profile"] == "workspace"
        assert value["listener_identity"]["executable"]["sha256"]
        assert value["listener_identity"]["hermes_entrypoint"]["sha256"]
        assert value["authority"]["managed_source"]["path"].endswith("/.hermes/profiles/workspace/config.yaml")
        assert set(value["authority"]["sections"]) == {"providers", "mcp_servers", "memory", "toolsets"}
    assert set(value["readiness"]) == {"gateway_liveness", "mcp", "rag", "memory", "toolsets"}
    assert value["remote_reachability"]["status"] == "UNVERIFIED"


def test_independent_live_captures_are_exact_except_time_and_nonce(tmp_path: Path) -> None:
    before = capture()
    after = capture()
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")
    completed = run("compare", str(before_path), str(after_path))
    assert completed.returncode == 0, completed.stderr
    comparison = json.loads(completed.stdout)
    assert comparison == {
        "status": "PASS",
        "equal": True,
        "excluded_fields": ["captured_at", "run_nonce"],
        "before_fingerprint": before["canonical_fingerprint_sha256"],
        "after_fingerprint": after["canonical_fingerprint_sha256"],
    }


def test_one_field_drift_fails_comparison(tmp_path: Path) -> None:
    before = capture()
    after = copy.deepcopy(before)
    after["bindings"]["dashboard_9119"]["address"] += "-drift"
    stable = {
        key: item
        for key, item in after.items()
        if key not in {"captured_at", "run_nonce", "canonical_fingerprint_sha256"}
    }
    after["canonical_fingerprint_sha256"] = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")
    completed = run("compare", str(before_path), str(after_path))
    assert completed.returncode == 3
    assert json.loads(completed.stdout)["equal"] is False


@pytest.mark.parametrize("_review_finding", [None], ids=["WR-06"])
def test_redirect_or_impostor_evidence_cannot_be_ready(_review_finding: None) -> None:
    value = capture()
    redirect = copy.deepcopy(value)
    redirect["status"] = "READY"
    redirect["go_eligible"] = True
    redirect["health"]["url"] = "http://impostor.invalid/health"
    assert list(VALIDATOR.iter_errors(redirect))

    impostor = copy.deepcopy(value)
    impostor["status"] = "READY"
    impostor["go_eligible"] = True
    impostor["listener_identity"] = {"status": "UNVERIFIED"}
    assert list(VALIDATOR.iter_errors(impostor))


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-04"])
def test_collector_contains_no_mutating_service_or_prime_commands(_review_finding: None) -> None:
    source = COLLECTOR.read_text(encoding="utf-8")
    for forbidden in ("systemctl restart", "service restart", "gateway run", "apply-hermes", "prime-agent --version", "PRIME_FEASIBILITY_TEST_MODE"):
        assert forbidden not in source
