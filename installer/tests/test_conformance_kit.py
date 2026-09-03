from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "modules" / "conformance-kit"


def load_runner():
    spec = importlib.util.spec_from_file_location("conformance_runner", KIT / "run-conformance.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_fake_harness_exercises_every_operation_provider_free():
    result = load_runner().execute(KIT / "fake-harness")
    assert result == {
        "status": "PASS",
        "harness": "provider-free-fake",
        "protocol_version": 2,
        "operations_exercised": 31,
        "provider_calls": 0,
        "pay_per_token": False,
        "core_unchanged": True,
        "failures": [],
    }


def test_conformance_suite_matches_operation_catalog_exactly():
    runner = load_runner()
    suite = runner.load_suite()
    catalog = json.loads((ROOT / "modules/orchestration-protocol/operation-catalog.json").read_text(encoding="utf-8"))
    assert {case["operation"] for case in suite["cases"]} == {row["name"] for row in catalog["operations"]}
    assert len(suite["cases"]) == 31


def test_fake_adapter_is_translation_only():
    runner = load_runner()
    adapter = KIT / "fake-harness/fake_adapter.py"
    assert runner.adapter_boundary_errors(adapter) == []
    text = adapter.read_text(encoding="utf-8")
    assert "from lapart_adapter import" in text
    assert "lapart_core" not in text
    assert len(text.splitlines()) < 80


def test_boundary_rejects_core_import_and_adapter_routing(tmp_path):
    runner = load_runner()
    adapter = tmp_path / "bad_adapter.py"
    adapter.write_text("from lapart_core import OrchestrationCore\ndef select_route():\n    return 'T0'\n", encoding="utf-8")
    errors = runner.adapter_boundary_errors(adapter)
    assert any("imports the shared core" in error for error in errors)
    assert any("core-owned methods" in error for error in errors)


def test_local_response_must_preserve_accounting():
    errors = load_runner().local_response_errors(
        {"provider_accounting": {"provider_calls": 1}},
        {"provider_calls": 0},
    )
    assert errors == ["adapter local response omitted or changed provider accounting"]


def test_conformance_source_hashes_are_current():
    assert load_runner().verify_source_hashes() == []


def test_fail_closed_mutations_are_all_rejected():
    assert load_runner().selftest() == []


def test_cli_returns_structured_provider_free_pass():
    process = subprocess.run(
        [sys.executable, str(KIT / "run-conformance.py"), "--selftest", "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(process.stdout)
    assert result["status"] == "PASS"
    assert result["operations_exercised"] == 31
    assert result["provider_calls"] == 0
    assert result["mutation_cases"] == 6


def test_module_manifest_installs_conformance_after_adapter_sdk():
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entries = {row["id"]: row for row in manifest["modules"]}
    assert manifest["default_path"][:4] == [
        "orchestration-protocol",
        "orchestration-core",
        "adapter-sdk",
        "conformance-kit",
    ]
    assert entries["conformance-kit"]["stage"] == 3
    assert entries["conformance-kit"]["required"] is True
