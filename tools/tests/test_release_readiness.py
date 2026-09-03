from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify-release-readiness.py"
SPEC = importlib.util.spec_from_file_location("release_readiness", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_release_documents_and_claims_are_current():
    assert MODULE.verify_documents() == []
    assert MODULE.verify_claims() == []


def test_public_payload_is_clean_and_source_history_hold_is_truthful():
    failures, summary = MODULE.verify()
    assert failures == []
    assert summary["public_payload_privacy_findings"] == 0
    assert summary["historical_source"]["public_source_gate"] == "HOLD"
    assert summary["runtime_activation"] == "unchanged"
    assert summary["provider_calls"] == 0


def test_adapter_inventory_gate_fails_on_missing_adapter(monkeypatch):
    original = json.loads((ROOT / "modules/adapters/compatibility-matrix.json").read_text(encoding="utf-8"))
    changed = copy.deepcopy(original)
    changed["adapters"].pop()
    original_loads = MODULE.json.loads

    def loads(text):
        if '"lapart.adapter-compatibility-matrix.v1"' in text:
            return changed
        return original_loads(text)

    monkeypatch.setattr(MODULE.json, "loads", loads)
    assert "adapter inventory" in MODULE.verify_claims()


def test_document_gate_fails_when_required_file_is_absent(monkeypatch):
    required = dict(MODULE.REQUIRED_DOCS)
    required["docs/v2/DOES-NOT-EXIST.md"] = ("missing",)
    monkeypatch.setattr(MODULE, "REQUIRED_DOCS", required)
    assert any(item.startswith("missing document:") for item in MODULE.verify_documents())
