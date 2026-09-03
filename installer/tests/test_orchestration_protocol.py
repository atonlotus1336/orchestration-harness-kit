import copy
import importlib.util
import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = ROOT / "modules" / "orchestration-protocol"
SCRIPT_PATH = MODULE_ROOT / "verify-protocol.py"
SPEC = importlib.util.spec_from_file_location("verify_protocol", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def load(name):
    return json.loads((MODULE_ROOT / name).read_text(encoding="utf-8"))


def make_envelope(operation, kind, payload):
    envelope = copy.deepcopy(load("fixtures.json")["positive"][0]["envelope"])
    envelope.pop("authentication", None)
    envelope["operation"] = operation
    envelope["kind"] = kind
    envelope["payload"] = payload
    return envelope


def test_catalog_schema_and_fixtures_pass():
    catalog = load("operation-catalog.json")
    schema = load("envelope.schema.json")
    fixtures = load("fixtures.json")
    assert MODULE.validate_catalog(catalog, schema) == []
    assert MODULE.validate_fixtures(fixtures, catalog, schema) == []
    assert MODULE.validate_source_hashes(load("source-hashes.json")) == []


def test_envelope_schema_is_valid_and_accepts_positive_fixtures():
    schema = load("envelope.schema.json")
    fixtures = load("fixtures.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    for case in fixtures["positive"]:
        assert list(validator.iter_errors(case["envelope"])) == []


def test_adapter_cannot_claim_core_policy():
    catalog = load("operation-catalog.json")
    schema = load("envelope.schema.json")
    catalog["adapter_owned"].append("authority_safety_policy")
    assert MODULE.validate_catalog(catalog, schema)


def test_unknown_top_level_field_fails_closed():
    catalog = load("operation-catalog.json")
    schema = load("envelope.schema.json")
    envelope = copy.deepcopy(load("fixtures.json")["positive"][0]["envelope"])
    envelope["credential"] = "not-allowed"
    assert MODULE.validate_envelope(envelope, catalog, schema)


def test_each_required_operation_has_request_and_response_contracts():
    catalog = load("operation-catalog.json")
    entries = MODULE.operation_map(catalog)
    assert set(entries) == MODULE.OPERATIONS
    assert all(entry["request_required"] and entry["response_required"] for entry in entries.values())


def test_protocol_module_is_required_on_the_default_install_path():
    manifest = json.loads(
        (ROOT / "installer" / "modules" / "module-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    entries = {entry["id"]: entry for entry in manifest["modules"]}
    assert manifest["default_path"][0] == "orchestration-protocol"
    assert entries["orchestration-protocol"]["required"] is True
    assert entries["orchestration-protocol"]["stage"] == 0


def test_retrieval_response_requires_source_attribution():
    catalog = load("operation-catalog.json")
    schema = load("envelope.schema.json")
    payload = {
        "sources": [
            {
                "source_reference": "source-1",
                "uri": "urn:example:source-1",
                "title": "Example source",
                "content_hash": "0" * 64,
            }
        ],
        "answer_context": "selected excerpt",
    }
    envelope = make_envelope("retrieval.query", "response", payload)
    assert MODULE.validate_envelope(envelope, catalog, schema) == []
    del envelope["payload"]["sources"][0]["content_hash"]
    assert MODULE.validate_envelope(envelope, catalog, schema)


def test_discovery_keeps_deferred_capabilities_visible():
    catalog = load("operation-catalog.json")
    schema = load("envelope.schema.json")
    for operation, key in (
        ("tool.discover", "tools"),
        ("skill.discover", "skills"),
        ("mcp.discover", "servers"),
    ):
        payload = {
            key: [{"id": "capability-1", "summary": "Example", "details_deferred": True}],
            "deferred_count": 1,
        }
        envelope = make_envelope(operation, "response", payload)
        assert MODULE.validate_envelope(envelope, catalog, schema) == []
        envelope["payload"]["deferred_count"] = 0
        assert MODULE.validate_envelope(envelope, catalog, schema)


def test_prime_and_verification_responses_are_deterministic_and_hashed():
    catalog = load("operation-catalog.json")
    schema = load("envelope.schema.json")
    prime = make_envelope(
        "routing.prime.select",
        "response",
        {"recommended_lane": "T2", "actual_lane": "T3", "rationale": "gated policy"},
    )
    assert MODULE.validate_envelope(prime, catalog, schema) == []
    prime["payload"]["actual_lane"] = "dynamic"
    assert MODULE.validate_envelope(prime, catalog, schema)

    verification = make_envelope(
        "verification.run",
        "response",
        {"decision": "PASS", "evidence": ["test-1"], "source_hashes": {"core": "a" * 64}},
    )
    assert MODULE.validate_envelope(verification, catalog, schema) == []
    verification["payload"]["source_hashes"]["core"] = "stale"
    assert MODULE.validate_envelope(verification, catalog, schema)


def test_provider_resolution_declares_route_class():
    catalog = load("operation-catalog.json")
    schema = load("envelope.schema.json")
    envelope = make_envelope(
        "provider.resolve",
        "response",
        {
            "provider_reference": "provider-1",
            "model_reference": "model-1",
            "route_class": "subscription",
            "rationale": "profile policy",
        },
    )
    assert MODULE.validate_envelope(envelope, catalog, schema) == []
    envelope["payload"]["route_class"] = "unaccounted"
    assert MODULE.validate_envelope(envelope, catalog, schema)
