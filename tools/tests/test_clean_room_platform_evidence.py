from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify-clean-room-platforms.py"
SPEC = importlib.util.spec_from_file_location("clean_room_platform_evidence", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def documents():
    return (
        json.loads(MODULE.WINDOWS_PATH.read_text(encoding="utf-8")),
        json.loads(MODULE.LINUX_PATH.read_text(encoding="utf-8")),
        json.loads(MODULE.AGGREGATE_PATH.read_text(encoding="utf-8")),
    )


def test_historical_platform_evidence_preserves_qualifier_hold():
    # Phase 80 is immutable evidence for its historical source. The qualifier
    # has evolved since then, so the old receipt must remain a precise HOLD
    # rather than being rewritten to describe current code.
    assert MODULE.verify() == ["qualifier source hash mismatch"]


@pytest.mark.parametrize("mutation, expected", [
    ("provider-call", "safety accounting"),
    ("artifact", "artifact mismatch"),
    ("missing-check", "check inventory"),
    ("hold", "decision"),
    ("wrong-commit", "source commit"),
])
def test_load_bearing_gates_fail_red(mutation, expected):
    windows, linux, aggregate = documents()
    if mutation == "provider-call":
        windows["provider_calls"] = 1
    elif mutation == "artifact":
        linux["artifacts"]["bundle_sha256"] = "0" * 64
    elif mutation == "missing-check":
        windows["checks"].pop()
    elif mutation == "hold":
        linux["decision"] = "HOLD"
    elif mutation == "wrong-commit":
        windows["source_commit"] = "0" * 40
    failures = MODULE.validate_documents(
        copy.deepcopy(windows),
        copy.deepcopy(linux),
        copy.deepcopy(aggregate),
        aggregate["artifacts"]["qualifier_sha256"],
        aggregate["verifier_sha256"],
    )
    assert any(expected in failure for failure in failures)
