from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate-prime-feasibility.sh"
ROUTE_TEMPLATE = ROOT / "tests" / "fixtures" / "model_routes" / "ollama_cloud.json"
SCHEMA = json.loads((ROOT / "contracts" / "prime-feasibility-report.schema.json").read_text(encoding="utf-8"))
HASH = "a" * 64


def as_runtime_path(path: str | Path) -> str:
    value = str(path)
    if value.startswith("/"):
        return value
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", value)
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2).replace(chr(92), '/')}"
    return str(Path(value).resolve())


def component(status: str = "PASS", upstream: str = "") -> dict[str, str]:
    return {"status": status, "sha256": HASH, "upstream_sha256": upstream}


def bundle(mode: str = "simulated") -> dict:
    names = ["provenance", "route", "image", "containment", "rpc", "scans", "native_before", "native_after", "context_preservation", "operator_authorization", "desktop"]
    return {
        "mode": mode,
        "prerequisite": {"status": "READY", "go_eligible": True, "sha256": HASH},
        "components": {name: component() for name in names},
        "scan_summary": {"generic_status": "PASS", "exact_status": "PASS", "credential_values_supplied": True},
        "launch": {"podman_build_started": False, "podman_run_started": True, "prime_child_started": True},
        "context": {
            "provider": "ollama", "model": "glm-5.2:cloud", "api": "openai-completions",
            "endpoint": "https://ollama.com/v1", "image_digest": "localhost/prime@sha256:" + HASH,
            "prerequisite_sha256": HASH, "native_before_fingerprint": HASH,
            "native_after_fingerprint": HASH, "operator_report_id": "b" * 32,
            "desktop_report_id": "b" * 32,
        },
    }


def fresh_route(tmp_path: Path) -> Path:
    value = json.loads(ROUTE_TEMPLATE.read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    value["api"] = value.pop("api_mode")
    value.update({
        "contract_version": "1.0", "active_profile": "home", "captured_at": now,
        "run_id": "route-12345678",
        "authority": {"source_path": "__WINDOWS_WORKSPACE__/.hermes/profiles/home/config.yaml", "source_sha256": HASH},
    })
    value["compatibility"]["verified_at"] = now
    path = tmp_path / "route.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def invoke(tmp_path: Path, value: dict, *, test_mode: bool = True) -> subprocess.CompletedProcess[str]:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(value), encoding="utf-8")
    environment = dict(os.environ)
    if test_mode:
        environment["PRIME_FEASIBILITY_TEST_MODE"] = "1"
    else:
        environment.pop("PRIME_FEASIBILITY_TEST_MODE", None)
    return subprocess.run(
        ["bash", str(VALIDATOR), "--route", str(fresh_route(tmp_path)), "--test-bundle", str(bundle_path)],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
    )


def parsed(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stdout, result.stderr
    value = json.loads(result.stdout)
    Draft202012Validator(SCHEMA).validate(value)
    return value


@pytest.mark.parametrize("_review_finding", [None], ids=["WR-07"])
def test_path_normalizer_accepts_native_posix_and_real_windows_drive_paths(_review_finding: None) -> None:
    assert as_runtime_path("/tmp/evidence.json") == "/tmp/evidence.json"
    assert as_runtime_path(r"__WINDOWS_WORKSPACE__\evidence.json") == "__WSL_WORKSPACE__/evidence.json"
    assert "/mnt//" not in as_runtime_path("relative/evidence.json")


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-05"])
def test_simulated_all_pass_is_permanently_non_authorizing(tmp_path: Path, _review_finding: None) -> None:
    result = invoke(tmp_path, bundle())
    value = parsed(result)
    assert result.returncode == 2
    assert value["source_kind"] == "SIMULATED"
    assert value["status"] == "UNVERIFIED"
    assert value["go_eligible"] is False
    assert value["decision_options"] == ["NO_GO"]
    assert value["human_decision"] is None
    assert {item["source_kind"] for item in value["components"].values()} == {"SIMULATED"}


def test_fixture_injection_is_rejected_outside_test_mode(tmp_path: Path) -> None:
    result = invoke(tmp_path, bundle(), test_mode=False)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "FeasibilityError" in result.stderr


def test_complete_podman_unavailable_chain_is_closed_and_nonlaunching(tmp_path: Path) -> None:
    value = bundle("unavailable")
    value["prerequisite"] = {"status": "UNAVAILABLE", "go_eligible": False, "sha256": HASH}
    for name in ("image", "containment", "rpc"):
        value["components"][name] = component("NOT_RUN", HASH)
    value["components"]["scans"] = component("PASS", HASH)
    value["scan_summary"] = {"generic_status": "PASS", "exact_status": "NOT_APPLICABLE", "credential_values_supplied": False}
    value["launch"] = {"podman_build_started": False, "podman_run_started": False, "prime_child_started": False}
    result = invoke(tmp_path, value)
    report = parsed(result)
    assert result.returncode == 2
    assert report["prerequisite"]["status"] == "UNAVAILABLE"
    assert [report["components"][name]["status"] for name in ("image", "containment", "rpc")] == ["NOT_RUN"] * 3
    assert report["components"]["scans"]["status"] == "PASS"
    assert report["launch"] == {"podman_build_started": False, "podman_run_started": False, "prime_child_started": False}


@pytest.mark.parametrize("forgery", ["ready_component", "wrong_hash", "claimed_launch"])
def test_malformed_or_forged_unavailable_chain_fails_instead_of_downgrading(tmp_path: Path, forgery: str) -> None:
    value = bundle("unavailable")
    value["prerequisite"] = {"status": "UNAVAILABLE", "go_eligible": False, "sha256": HASH}
    for name in ("image", "containment", "rpc"):
        value["components"][name] = component("NOT_RUN", HASH)
    value["scan_summary"] = {"generic_status": "PASS", "exact_status": "NOT_APPLICABLE", "credential_values_supplied": False}
    value["launch"] = {"podman_build_started": False, "podman_run_started": False, "prime_child_started": False}
    if forgery == "ready_component":
        value["components"]["rpc"]["status"] = "PASS"
    elif forgery == "wrong_hash":
        value["components"]["containment"]["upstream_sha256"] = "b" * 64
    else:
        value["launch"]["prime_child_started"] = True
    result = invoke(tmp_path, value)
    assert result.returncode == 1
    assert result.stdout == ""


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-06"])
def test_one_field_native_drift_fails_context_preservation(tmp_path: Path, _review_finding: None) -> None:
    value = bundle()
    value["components"]["native_after"]["sha256"] = "b" * 64
    result = invoke(tmp_path, value)
    report = parsed(result)
    assert report["components"]["context_preservation"]["status"] == "FAIL"
    assert "native_context_drift" in report["reasons"]
    assert report["go_eligible"] is False


def test_ready_schema_requires_every_conjunct_and_never_human_choice() -> None:
    value = bundle()
    report = {
        "schema_version": "1.0", "report_id": "b" * 32, "captured_at": "2026-08-11T00:00:00Z",
        "source_kind": "LIVE", "status": "READY", "go_eligible": True,
        "decision_options": ["GO", "NO_GO"], "human_decision": None,
        "prerequisite": value["prerequisite"],
        "components": {name: {**item, "source_kind": "LIVE"} for name, item in value["components"].items()},
        "scan_summary": value["scan_summary"], "launch": value["launch"], "context": value["context"], "reasons": [],
    }
    Draft202012Validator(SCHEMA).validate(report)
    for name in (item for item in report["components"] if item != "desktop"):
        broken = copy.deepcopy(report)
        broken["components"][name]["status"] = "UNVERIFIED"
        assert list(Draft202012Validator(SCHEMA).iter_errors(broken)), name
        replayed = copy.deepcopy(report)
        replayed["components"][name]["source_kind"] = "SIMULATED"
        assert list(Draft202012Validator(SCHEMA).iter_errors(replayed)), name
    deferred = copy.deepcopy(report)
    deferred["components"]["desktop"]["status"] = "UNVERIFIED"
    deferred["context"]["desktop_report_id"] = ""
    deferred["reasons"] = ["desktop_evidence_deferred"]
    Draft202012Validator(SCHEMA).validate(deferred)
    selected = copy.deepcopy(report)
    selected["human_decision"] = "GO"
    assert list(Draft202012Validator(SCHEMA).iter_errors(selected))


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-10"])
def test_validator_requires_consumed_ledger_not_schema_valid_desktop_json(_review_finding: None) -> None:
    source = VALIDATOR.read_text(encoding="utf-8")
    assert "consumed_evidence_matches(DESKTOP_STORE, desktop)" in source
    assert "desktop_report_id == report_id" in source
    assert "consumed_evidence_matches(DESKTOP_STORE, operator)" in source
    assert 'operator["client_id"] == "NUCBOX-MINI-ULTRA"' in source


def test_live_without_both_opt_ins_closes_after_independent_native_captures(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("PRIME_LIVE_PROBE", None)
    completed = subprocess.run(
        ["bash", str(VALIDATOR), "--route", str(fresh_route(tmp_path)), "--report", str(tmp_path / "report.json")],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
    )
    report = parsed(completed)
    assert completed.returncode == 2
    assert report["source_kind"] == "LIVE"
    assert report["go_eligible"] is False
    assert report["components"]["native_before"]["status"] == "PASS"
    assert report["components"]["native_after"]["status"] == "PASS"
    assert report["components"]["rpc"]["status"] == "NOT_RUN"
    assert report["components"]["route"]["status"] == "PASS"
    assert report["scan_summary"] == {"generic_status": "PASS", "exact_status": "NOT_APPLICABLE", "credential_values_supplied": False}
    assert report["launch"]["prime_child_started"] is False
    assert report["human_decision"] is None


@pytest.mark.parametrize("_review_finding", [None], ids=["WR-05"])
def test_malformed_live_route_still_emits_structured_closed_report(tmp_path: Path, _review_finding: None) -> None:
    route = tmp_path / "malformed-route.json"
    route.write_text("{", encoding="utf-8")
    report_path = tmp_path / "report.json"
    environment = dict(os.environ)
    environment.pop("PRIME_LIVE_PROBE", None)
    completed = subprocess.run(
        ["bash", str(VALIDATOR), "--route", str(route), "--report", str(report_path)],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
    )
    report = parsed(completed)
    assert completed.returncode == 2
    assert report_path.is_file()
    assert report["go_eligible"] is False and report["decision_options"] == ["NO_GO"]
    assert "aggregation_error:JSONDecodeError" in report["reasons"]
    assert report["components"]["native_before"]["status"] == "PASS"
    assert report["components"]["native_after"]["status"] == "PASS"


def test_review_finding_inventory_is_exactly_collected_and_has_no_skip_markers() -> None:
    expected = {f"CR-{index:02d}" for index in range(1, 11)} | {f"WR-{index:02d}" for index in range(1, 8)}
    files = (
        "tests/integration/test_prime_feasibility.py",
        "tests/integration/test_prime_baseline_contract.py",
        "tests/integration/test_prime_install_contract.py",
        "tests/integration/test_prime_podman_sandbox.py",
        "tests/prime_bridge/test_model_route.py",
        "tests/prime_bridge/test_rpc_probe.py",
        "tests/prime_bridge/test_sandbox.py",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *files],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    counts = Counter(re.findall(r"(?:CR|WR)-\d{2}", completed.stdout))
    assert set(counts) == expected
    assert all(counts[finding] == 1 for finding in expected)
    forbidden_skip_markers = ("pytest." + "skip", "@pytest.mark." + "skip")
    for relative in files:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert all(marker not in source for marker in forbidden_skip_markers)
