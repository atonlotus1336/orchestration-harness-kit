from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES = ROOT / "modules/adapters/hermes"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_hermes_reference_adapter_passes_provider_free_conformance():
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", "hermes_test_conformance")
    result = runner.execute(HERMES)
    assert result["status"] == "PASS"
    assert result["operations_exercised"] == 31
    assert result["provider_calls"] == 0
    assert result["pay_per_token"] is False
    assert result["core_unchanged"] is True


def test_all_protected_profiles_are_source_only_and_unchanged():
    value = json.loads((HERMES / "profile-matrix.json").read_text(encoding="utf-8"))
    assert {row["id"] for row in value["profiles"]} == {
        "home", "workspace", "nucbox-telegram", "vps-telegram",
    }
    assert all(row["qualification"] == "source-qualified" for row in value["profiles"])
    assert all(row["runtime_state"] == "unchanged" for row in value["profiles"])
    assert all(row["activation_changed"] is False for row in value["profiles"])


def test_hermes_adapter_is_translation_only_and_closed():
    runner = load(ROOT / "modules/conformance-kit/run-conformance.py", "hermes_test_boundary")
    adapter = HERMES / "hermes_adapter.py"
    assert runner.adapter_boundary_errors(adapter) == []
    text = adapter.read_text(encoding="utf-8")
    assert "from lapart_adapter import" in text
    assert "lapart_core" not in text
    assert len(text.splitlines()) < 90


def test_hermes_verifier_accepts_current_sources():
    verifier = load(HERMES / "verify-hermes-adapter.py", "hermes_test_verifier")
    assert verifier.verify() == []


def test_installer_exposes_hermes_as_opt_in_reference_adapter():
    manifest = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entries = {row["id"]: row for row in manifest["modules"]}
    assert entries["adapter-hermes"]["required"] is False
    assert entries["adapter-hermes"]["source"] == "modules/adapters/hermes"
    assert "adapter-hermes" not in manifest["default_path"]
