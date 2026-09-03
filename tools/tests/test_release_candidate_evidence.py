from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify-release-candidate.py"
SPEC = importlib.util.spec_from_file_location("release_candidate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def documents():
    receipts = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in MODULE.RECEIPTS.items()}
    aggregate = json.loads(MODULE.AGGREGATE_PATH.read_text(encoding="utf-8"))
    return receipts, aggregate


def test_current_release_candidate_evidence_passes():
    assert MODULE.verify() == []


@pytest.mark.parametrize("mutation, expected", [
    ("provider-call", "safety accounting"),
    ("artifact", "artifact mismatch"),
    ("missing-check", "check inventory"),
    ("source", "source"),
    ("publication", "publication boundary"),
    ("activation", "activation boundary"),
])
def test_load_bearing_release_gates_fail_red(mutation, expected):
    receipts, aggregate = documents()
    if mutation == "provider-call":
        receipts["docker-linux"]["provider_calls"] = 1
    elif mutation == "artifact":
        receipts["linux-wsl-ubuntu-restored"]["artifacts"]["bundle_sha256"] = "0" * 64
    elif mutation == "missing-check":
        receipts["windows-native"]["checks"].pop()
    elif mutation == "source":
        receipts["docker-linux"]["source_commit"] = "0" * 40
    elif mutation == "publication":
        aggregate["publication"]["published"] = True
    elif mutation == "activation":
        aggregate["activation"] = "production"
    failures = MODULE.validate_documents(copy.deepcopy(receipts), copy.deepcopy(aggregate), aggregate["verifier_sha256"])
    assert any(expected in failure for failure in failures)
