"""Phase 10 Plan 01 feasibility gate tests (fail-closed)."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts" / "probe-omp-feasibility.py"
SCHEMA = json.loads((ROOT / "config" / "omp-feasibility.schema.json").read_text(encoding="utf-8"))
LOCK = ROOT / "config" / "omp-runtime-lock.json"
PHASE9_SUMMARY = ROOT / ".planning" / "phases" / "09-automatic-prime-readiness-gsd-routing" / "09-04-SUMMARY.md"


def load_probe_module():
    spec = importlib.util.spec_from_file_location("omp_feasibility_probe", PROBE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_probe(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROBE), *args],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )


def parsed(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stdout, result.stderr
    value = json.loads(result.stdout)
    Draft202012Validator(SCHEMA).validate(value)
    return value


def test_schema_is_closed_and_typed() -> None:
    assert SCHEMA["properties"]["verdict"]["enum"] == [
        "READY", "PHASE9_BLOCKED", "RUNTIME_UNAVAILABLE", "MODEL_INCOMPATIBLE", "OMP_RUNTIME_UNSAFE",
    ]
    assert SCHEMA["additionalProperties"] is False


def test_probe_emits_schema_valid_report() -> None:
    result = run_probe()
    report = parsed(result)
    assert report["schema_version"] == "1.0"
    assert report["verdict"] in SCHEMA["properties"]["verdict"]["enum"]
    assert report["phase9"]["status"] in {"PASS", "BLOCKED", "NOT_RUN"}
    assert report["runtime_lock"]["status"] in {"PASS", "FAIL", "NOT_RUN"}
    assert report["sdk_probe"]["status"] in {"PASS", "FAIL", "NOT_RUN"}


def test_phase9_gate_is_fail_closed() -> None:
    """Without a canonical live route receipt the verdict must be PHASE9_BLOCKED."""
    result = run_probe()
    report = parsed(result)
    if report["phase9"]["status"] != "PASS":
        assert report["verdict"] == "PHASE9_BLOCKED"
        assert "phase9_live_route_receipt_missing" in report["reasons"]


def test_runtime_lock_pins_exact_package_and_bun() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert lock["package"]["name"] == "@oh-my-pi/pi-coding-agent"
    assert lock["package"]["version"] == "17.3.0"
    assert lock["package"]["integrity"] == "sha512-Ad2VB+dRxmKACarbq7eM9/cjfWlZWeKBliqAP5OpWU8vrj8i4xLVtuJpTJ3q3Ile9Tau2YbzBSJeWHDNAMtIhw=="
    assert lock["bun"]["minimum"] == "1.3.14"


def test_phase9_summary_does_not_claim_live_readiness() -> None:
    """The Phase 9 summary must not be mistaken for a live route receipt."""
    if not PHASE9_SUMMARY.is_file():
        return  # Planning prose is intentionally excluded from the snapshot.
    text = PHASE9_SUMMARY.read_text(encoding="utf-8")
    # The summary explicitly defers the live checkpoint; a live claim would be a false positive.
    assert "Remaining live checkpoint" in text


def test_strict_mode_fails_closed_on_non_ready() -> None:
    result = run_probe("--strict")
    report = parsed(result)
    if report["verdict"] != "READY":
        assert result.returncode == 2


def test_windows_dispatch_uses_configured_wsl_runtime(monkeypatch, capsys) -> None:
    module = load_probe_module()
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(command, 0, '{"verdict":"READY"}\n', "")

    monkeypatch.setattr(module, "_canonical_wsl_script_path", lambda: "/mnt/d/kit/probe.py")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module._rerun_in_canonical_wsl(["--strict"]) == 0
    assert observed["command"][:4] == ["wsl.exe", "-d", module.WSL_DISTRO, "--"]
    assert observed["command"][-2:] == ["/mnt/d/kit/probe.py", "--strict"]
    assert json.loads(capsys.readouterr().out)["verdict"] == "READY"
