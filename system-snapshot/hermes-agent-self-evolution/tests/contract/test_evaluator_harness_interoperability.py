
import hashlib
import json
from pathlib import Path

import pytest

from prime_bridge.adaptive_review.harness_ingress import (
    HarnessIngressError,
    load_adapter_registry,
    normalize_review_submission,
)
from prime_bridge.harness_registry import UNIVERSAL_HARNESS_REGISTRY

ROOT = Path(__file__).resolve().parents[2]


def submission(harness):
    body = "def value():\n    return 42\n"
    return {
        "schema_version": "1.0",
        "harness": harness,
        "goal_id": "goal-1",
        "receipt_id": "receipt-1",
        "attempt_id": "attempt-1",
        "source_identity": "snapshot-1",
        "source_hashes": {"src/value.py": hashlib.sha256(body.encode()).hexdigest()},
        "rubric": "Report correctness findings only.",
        "content": {"src/value.py": body},
        "deadline_ms": 30000,
        "reserved_budget": {
            "max_context_bytes": 4096,
            "max_model_calls": 1,
            "max_output_tokens": 256,
            "max_total_tokens": 1024,
            "max_elapsed_seconds": 30,
        },
        "capability_attestation": {
            "tool_names": [],
            "source_mounted": False,
            "patch_channel": False,
            "arbitrary_command": False,
        },
    }


def test_registry_matches_canonical_harness_inventory_and_is_inactive():
    registry = load_adapter_registry()
    assert tuple(registry) == UNIVERSAL_HARNESS_REGISTRY
    config = json.loads((ROOT / "config/evaluator-harness-adapters.json").read_text())
    assert config["activation_state"] == "inactive"
    assert all("transport" not in spec for spec in config["adapters"].values())


def test_every_harness_normalizes_to_identical_evaluator_payload_and_hash():
    normalized = [
        normalize_review_submission(submission(harness))
        for harness in UNIVERSAL_HARNESS_REGISTRY
    ]
    assert {item.packet_sha256 for item in normalized} == {normalized[0].packet_sha256}
    assert {json.dumps(item.payload, sort_keys=True) for item in normalized} == {
        json.dumps(normalized[0].payload, sort_keys=True)
    }
    assert {item.ingress_harness for item in normalized} == set(UNIVERSAL_HARNESS_REGISTRY)


@pytest.mark.parametrize("field", ["transport", "reviewer_transport", "herdr_session"])
def test_harness_cannot_select_or_address_transport(field):
    value = submission("codex")
    value[field] = "herdr"
    with pytest.raises(HarnessIngressError, match="closed contract"):
        normalize_review_submission(value)


def test_unknown_harness_and_capability_claims_fail_closed():
    with pytest.raises(HarnessIngressError, match="unsupported harness"):
        normalize_review_submission(submission("unknown"))

    value = submission("codex")
    value["capability_attestation"]["source_mounted"] = True
    with pytest.raises(HarnessIngressError, match="report-only"):
        normalize_review_submission(value)


def test_content_hash_and_scope_are_immutable():
    value = submission("hermes")
    value["content"]["src/value.py"] = "changed"
    with pytest.raises(HarnessIngressError, match="source hash"):
        normalize_review_submission(value)

    value = submission("hermes")
    value["content"]["other.py"] = "extra"
    with pytest.raises(HarnessIngressError, match="scope mismatch"):
        normalize_review_submission(value)
