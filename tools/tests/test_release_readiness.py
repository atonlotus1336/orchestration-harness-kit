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
    assert MODULE.scan_public_payload(ROOT) == []
    history = MODULE.history_summary()
    assert history["public_source_gate"] == "HOLD"


def test_adapter_inventory_gate_fails_on_missing_adapter(monkeypatch):
    original = json.loads((ROOT / "modules/adapters/compatibility-matrix.json").read_text(encoding="utf-8"))
    changed = copy.deepcopy(original)
    changed["adapters"].pop()
    original_loads = MODULE.json.loads

    def loads(text):
        if '"lapart.adapter-compatibility-matrix.v2"' in text:
            return changed
        return original_loads(text)

    monkeypatch.setattr(MODULE.json, "loads", loads)
    assert "adapter inventory" in MODULE.verify_claims()


def test_document_gate_fails_when_required_file_is_absent(monkeypatch):
    required = dict(MODULE.REQUIRED_DOCS)
    required["docs/v2/DOES-NOT-EXIST.md"] = ("missing",)
    monkeypatch.setattr(MODULE, "REQUIRED_DOCS", required)
    assert any(item.startswith("missing document:") for item in MODULE.verify_documents())


def test_claim_gate_fails_when_recipient_payload_omits_distribution_notice(monkeypatch):
    original_loads = MODULE.json.loads

    def loads(text):
        value = original_loads(text)
        if isinstance(value, dict) and value.get("version") == 1 and "groups" in value:
            value = copy.deepcopy(value)
            value["groups"]["core"].remove("THIRD-PARTY-NOTICES.md")
        return value

    monkeypatch.setattr(MODULE.json, "loads", loads)
    assert "recipient payload omits distribution notices" in MODULE.verify_claims()


def test_stable_tag_gate_ignores_older_release_tag(monkeypatch):
    monkeypatch.setattr(MODULE, "_git", lambda *args: "v2.0.0" if args == ("tag", "--list") else "")
    assert "stable tag does not identify HEAD" not in MODULE.verify_claims()


def test_stable_tag_gate_rejects_mismatched_current_release_tag(monkeypatch):
    current_tag = f"v{MODULE.VERSION}"

    def fake_git(*args):
        if args == ("tag", "--list"):
            return current_tag
        if args == ("rev-parse", f"{current_tag}^{{commit}}"):
            return "old-commit"
        if args == ("rev-parse", "HEAD"):
            return "new-commit"
        raise AssertionError(args)

    monkeypatch.setattr(MODULE, "_git", fake_git)
    assert "stable tag does not identify HEAD" in MODULE.verify_claims()
