
import hashlib
import json
from pathlib import Path

from jsonschema import Draft7Validator, RefResolver

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"


def load(name):
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def validator(name):
    schema = load(name)
    names = (
        "adaptive-code-review-finding.schema.json",
        "herdr-review-request.schema.json",
        "herdr-review-result.schema.json",
        "evaluator-harness-request.schema.json",
    )
    store = {
        other_schema.get("$id", other): other_schema
        for other in names
        for other_schema in (load(other),)
    }
    return Draft7Validator(
        schema,
        resolver=RefResolver(base_uri=SCHEMAS.as_uri() + "/", referrer=schema, store=store),
    )


def budget():
    return {
        "max_context_bytes": 4096,
        "max_model_calls": 1,
        "max_output_tokens": 256,
        "max_total_tokens": 1024,
        "max_elapsed_seconds": 30,
    }


def request():
    body = "print('ok')"
    return {
        "schema_version": "1.0",
        "receipt_id": "receipt-1",
        "attempt_id": "attempt-1",
        "reviewer_id": "correctness",
        "s_level": "S1",
        "packet_sha256": "a" * 64,
        "source_identity": "snapshot-1",
        "source_hashes": {"src/app.py": hashlib.sha256(body.encode()).hexdigest()},
        "rubric": "Report correctness findings only.",
        "content": {"src/app.py": body},
        "deadline_ms": 30000,
        "reserved_budget": budget(),
    }


def result():
    return {
        "schema_version": "1.0",
        "receipt_id": "receipt-1",
        "attempt_id": "attempt-1",
        "reviewer_id": "correctness",
        "packet_sha256": "a" * 64,
        "source_identity": "snapshot-1",
        "findings": [],
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        "started_at": "2026-09-12T10:00:00Z",
        "finished_at": "2026-09-12T10:00:01Z",
    }


def test_new_schemas_are_valid_draft7():
    for name in (
        "herdr-review-request.schema.json",
        "herdr-review-result.schema.json",
        "evaluator-harness-request.schema.json",
    ):
        Draft7Validator.check_schema(load(name))


def test_herdr_request_and_result_accept_closed_valid_envelopes():
    assert list(validator("herdr-review-request.schema.json").iter_errors(request())) == []
    assert list(validator("herdr-review-result.schema.json").iter_errors(result())) == []


def test_herdr_contracts_reject_mutation_and_missing_lineage_fields():
    bad_request = request()
    bad_request["commands"] = ["git diff"]
    assert list(validator("herdr-review-request.schema.json").iter_errors(bad_request))

    bad_result = result()
    bad_result["patch"] = "forbidden"
    assert list(validator("herdr-review-result.schema.json").iter_errors(bad_result))

    missing = result()
    del missing["attempt_id"]
    assert list(validator("herdr-review-result.schema.json").iter_errors(missing))


def test_harness_contract_cannot_choose_transport_or_claim_capabilities():
    body = "print('ok')"
    value = {
        "schema_version": "1.0",
        "harness": "codex",
        "goal_id": "goal-1",
        "receipt_id": "receipt-1",
        "attempt_id": "attempt-1",
        "source_identity": "snapshot-1",
        "source_hashes": {"src/app.py": hashlib.sha256(body.encode()).hexdigest()},
        "rubric": "Review correctness.",
        "content": {"src/app.py": body},
        "deadline_ms": 30000,
        "reserved_budget": budget(),
        "capability_attestation": {
            "tool_names": [],
            "source_mounted": False,
            "patch_channel": False,
            "arbitrary_command": False,
        },
    }
    check = validator("evaluator-harness-request.schema.json")
    assert list(check.iter_errors(value)) == []

    transport_request = dict(value, transport="herdr")
    assert list(check.iter_errors(transport_request))

    capable = json.loads(json.dumps(value))
    capable["capability_attestation"]["tool_names"] = ["shell"]
    assert list(check.iter_errors(capable))
