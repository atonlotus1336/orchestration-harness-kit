#!/usr/bin/env python3
"""Provider-free verifier for Universal Orchestration Protocol 2."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parent
KIT_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(KIT_ROOT))
from installer.lib.versioning import IncompatibleProtocolError, negotiate_protocol


CATALOG_PATH = MODULE_ROOT / "operation-catalog.json"
SCHEMA_PATH = MODULE_ROOT / "envelope.schema.json"
FIXTURES_PATH = MODULE_ROOT / "fixtures.json"
SOURCE_HASHES_PATH = MODULE_ROOT / "source-hashes.json"

OPERATIONS = {
    "lifecycle.initialize",
    "lifecycle.shutdown",
    "capabilities.negotiate",
    "session.create",
    "session.continue",
    "session.detach",
    "session.reattach",
    "message.submit",
    "message.stream",
    "mcp.discover",
    "mcp.connect",
    "tool.discover",
    "tool.invoke",
    "skill.discover",
    "skill.load",
    "memory.read",
    "memory.write",
    "retrieval.query",
    "routing.prime.select",
    "execution.omp.request",
    "execution.omp.patch",
    "authority.decide",
    "context.attribute",
    "optimization.plan",
    "provider.resolve",
    "provider.account",
    "telemetry.report",
    "health.readiness",
    "activation.transition",
    "rollback.execute",
    "verification.run",
}
CORE_OWNED = {
    "prime_routing",
    "governed_omp_execution",
    "authority_safety_policy",
    "skills_capability_discovery",
    "progressive_tool_disclosure",
    "mcp_connectivity",
    "shared_memory",
    "rag_retrieval_routing",
    "context_selection",
    "token_compute_optimization",
    "provider_model_policy",
    "provider_call_accounting",
    "telemetry",
    "health_reporting",
    "activation_state",
    "rollback_contracts",
    "deterministic_verification",
}
ADAPTER_OWNED = {
    "session_message_translation",
    "invocation_streaming_translation",
    "tool_call_translation",
    "authentication_reference_translation",
    "lifecycle_translation",
    "configuration_location_translation",
    "health_rollback_signal_translation",
}
TELEMETRY_METRIC_KEYS = {"latency_ms", "stage_timings_ms"}
TELEMETRY_STAGES = {
    "adapter", "routing", "provider_wait", "tool_execution", "handoff",
    "evidence_collection", "persistence",
}
TOP_LEVEL_REQUIRED = {
    "protocol_version",
    "message_id",
    "kind",
    "operation",
    "harness",
    "session",
    "payload",
}
ATTRIBUTION_FIELDS = {
    "system_prompt_bytes",
    "authority_context_bytes",
    "skills_index_bytes",
    "tool_schema_bytes",
    "selected_content_bytes",
    "rendered_total_bytes",
    "saved_bytes",
}
ACCOUNTING_FIELDS = {
    "provider_calls",
    "input_tokens",
    "output_tokens",
    "route_class",
    "pay_per_token",
}
FORBIDDEN_CREDENTIAL_VALUE_KEYS = {
    "api_key",
    "credential",
    "credential_value",
    "password",
    "secret",
    "token",
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def operation_map(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in catalog.get("operations", [])}


def validate_catalog(catalog: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if catalog.get("schema") != "lapart.operation-catalog.v2":
        errors.append("unexpected operation catalog schema")
    if catalog.get("protocol_version") != 2 or catalog.get("compatible_versions") != [1, 2]:
        errors.append("catalog version compatibility must be [1, 2]")
    if catalog.get("operation_processor") != "shared_core":
        errors.append("the shared core must process every operation")

    core = set(catalog.get("core_owned", []))
    adapter = set(catalog.get("adapter_owned", []))
    if core != CORE_OWNED:
        errors.append("shared-core ownership list is incomplete or changed")
    if adapter != ADAPTER_OWNED:
        errors.append("adapter ownership must contain translation responsibilities only")
    if core.intersection(adapter):
        errors.append("adapter ownership overlaps shared-core policy")

    entries = catalog.get("operations", [])
    names = [entry.get("name") for entry in entries]
    if len(names) != len(set(names)):
        errors.append("operation catalog contains duplicate names")
    if set(names) != OPERATIONS:
        errors.append("operation catalog coverage is incomplete or changed")
    for entry in entries:
        if not entry.get("request_required") or not entry.get("response_required"):
            errors.append(f"operation {entry.get('name')} lacks request or response fields")

    schema_operations = set(
        schema.get("properties", {}).get("operation", {}).get("enum", [])
    )
    if schema_operations != OPERATIONS:
        errors.append("envelope schema and operation catalog disagree")
    if set(schema.get("required", [])) != TOP_LEVEL_REQUIRED:
        errors.append("envelope top-level required fields changed")
    if schema.get("additionalProperties") is not False:
        errors.append("envelope must reject unknown top-level fields")
    return errors


def _validate_nonnegative_fields(value: Any, fields: set[str], label: str) -> list[str]:
    if not isinstance(value, dict) or set(value) != fields:
        return [f"{label} fields are incomplete or unknown"]
    numeric = fields - {"route_class", "pay_per_token"}
    if any(isinstance(value[name], bool) or not isinstance(value[name], int) or value[name] < 0 for name in numeric):
        return [f"{label} byte or token counts must be nonnegative integers"]
    return []


def _credential_value_paths(value: Any, path: str = "payload") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.lower() in FORBIDDEN_CREDENTIAL_VALUE_KEYS:
                found.append(child_path)
            found.extend(_credential_value_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_credential_value_paths(child, f"{path}[{index}]"))
    return found


def _valid_source_hashes(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(isinstance(name, str) and isinstance(digest, str) and HASH_RE.fullmatch(digest) for name, digest in value.items())
    )


def validate_envelope(envelope: dict[str, Any], catalog: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed = set(schema.get("properties", {}))
    missing = TOP_LEVEL_REQUIRED - set(envelope)
    unknown = set(envelope) - allowed
    if missing:
        errors.append("missing envelope fields: " + ", ".join(sorted(missing)))
    if unknown:
        errors.append("unknown envelope fields: " + ", ".join(sorted(unknown)))
    if errors:
        return errors

    if envelope.get("protocol_version") not in {1, 2}:
        errors.append("unsupported protocol version")
    if envelope.get("kind") not in {"request", "response", "event", "error"}:
        errors.append("invalid envelope kind")
    operation = envelope.get("operation")
    operations = operation_map(catalog)
    if operation not in operations:
        errors.append("unknown operation")

    harness = envelope.get("harness")
    if not isinstance(harness, dict) or set(harness) != {"name", "version", "adapter_version"}:
        errors.append("invalid harness identity")
    session = envelope.get("session")
    if not isinstance(session, dict) or set(session) != {"id", "state"}:
        errors.append("invalid session identity")
    elif session.get("state") not in {"new", "active", "detached", "closed", "failed"}:
        errors.append("invalid session state")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
    elif _credential_value_paths(payload):
        errors.append("credential values are forbidden; use authentication references")

    authentication = envelope.get("authentication")
    if authentication is not None:
        if not isinstance(authentication, dict) or set(authentication) != {"kind", "reference"}:
            errors.append("authentication may contain references only")
        elif authentication.get("kind") not in {"environment", "secret_store", "managed_identity"}:
            errors.append("invalid authentication reference kind")

    kind = envelope.get("kind")
    if operation in operations and isinstance(payload, dict) and kind in {"request", "response"}:
        required_key = f"{kind}_required"
        required = set(operations[operation][required_key])
        absent = required - set(payload)
        if absent:
            errors.append(f"missing {kind} payload fields: " + ", ".join(sorted(absent)))

    if operation in {"context.attribute", "optimization.plan"} and kind == "response":
        attribution = envelope.get("context_attribution")
        if attribution is None:
            errors.append("context attribution is required")
        else:
            errors.extend(_validate_nonnegative_fields(attribution, ATTRIBUTION_FIELDS, "context attribution"))

    if operation == "provider.account" and kind == "response":
        accounting = envelope.get("provider_accounting")
        if accounting is None:
            errors.append("provider accounting is required")
        else:
            errors.extend(_validate_nonnegative_fields(accounting, ACCOUNTING_FIELDS, "provider accounting"))
            if isinstance(accounting, dict) and set(accounting) == ACCOUNTING_FIELDS:
                route = accounting["route_class"]
                if not isinstance(accounting["pay_per_token"], bool):
                    errors.append("pay-per-token accounting flag must be boolean")
                if route not in {"provider_free", "subscription", "pay_per_token"}:
                    errors.append("invalid provider route class")
                if accounting["pay_per_token"] is not (route == "pay_per_token"):
                    errors.append("pay-per-token flag and route class disagree")
                if route == "provider_free" and any(
                    accounting[name] != 0 for name in ("provider_calls", "input_tokens", "output_tokens")
                ):
                    errors.append("provider-free accounting must contain explicit zero counts")

    if operation == "activation.transition" and kind == "request" and isinstance(payload, dict):
        states = {"inactive", "shadow", "review", "production"}
        if payload.get("previous_state") not in states or payload.get("requested_state") not in states:
            errors.append("invalid activation state")
        required_gates = {"quality", "calls", "latency", "rollback", "tokens"}
        if not required_gates.issubset(set(payload.get("gates", []))):
            errors.append("activation transition lacks required promotion gates")

    if operation == "retrieval.query" and kind == "response" and isinstance(payload, dict):
        sources = payload.get("sources")
        required_source = {"source_reference", "uri", "title", "content_hash"}
        if not isinstance(sources, list) or any(
            not isinstance(source, dict) or set(source) != required_source
            for source in sources
        ):
            errors.append("retrieval sources require complete attribution")

    if operation in {"tool.discover", "skill.discover", "mcp.discover"} and kind == "response" and isinstance(payload, dict):
        collection_key = {"tool.discover": "tools", "skill.discover": "skills", "mcp.discover": "servers"}[operation]
        items = payload.get(collection_key)
        if not isinstance(items, list) or any(
            not isinstance(item, dict)
            or set(item) != {"id", "summary", "details_deferred"}
            or not isinstance(item["details_deferred"], bool)
            for item in items
        ):
            errors.append("discovery summaries must preserve deferred capability references")
        if isinstance(items, list) and payload.get("deferred_count") != sum(
            1 for item in items if isinstance(item, dict) and item.get("details_deferred") is True
        ):
            errors.append("discovery deferred count is inaccurate")

    if operation == "routing.prime.select" and kind == "response" and isinstance(payload, dict):
        lanes = {"T0", "T1", "T2", "T3", "T4"}
        if payload.get("recommended_lane") not in lanes or payload.get("actual_lane") not in lanes:
            errors.append("Prime response requires deterministic T0-T4 lanes")

    if operation in {"execution.omp.patch", "verification.run"} and kind == "response" and isinstance(payload, dict):
        if not _valid_source_hashes(payload.get("source_hashes")):
            errors.append("verification source hashes must be nonempty SHA-256 mappings")

    if operation == "provider.resolve" and kind == "response" and isinstance(payload, dict):
        if payload.get("route_class") not in {"provider_free", "subscription", "pay_per_token"}:
            errors.append("provider resolution has an invalid route class")
        backend_fit = payload.get("backend_fit")
        lease = payload.get("lease")
        qualification = payload.get("qualification")
        if backend_fit is not None and (
            not isinstance(backend_fit, dict) or backend_fit.get("status") != "FIT"
        ):
            errors.append("provider backend-fit extension is invalid")
        if lease is not None and (
            not isinstance(lease, dict) or lease.get("terms_status") not in {"current", "stale", "unverified"}
        ):
            errors.append("provider lease extension is invalid")
        if qualification is not None and (
            not isinstance(qualification, dict) or qualification.get("status") not in {"qualified", "stale", "unqualified"}
        ):
            errors.append("provider qualification extension is invalid")

    if operation == "telemetry.report" and isinstance(payload, dict):
        metrics = payload.get("metrics") if kind == "request" else None
        if kind == "request":
            if not isinstance(metrics, dict) or set(metrics) - TELEMETRY_METRIC_KEYS:
                errors.append("telemetry metrics must be a closed content-free object")
            else:
                latency = metrics.get("latency_ms")
                if latency is not None and (type(latency) is not int or latency < 0):
                    errors.append("telemetry latency is invalid")
                stages = metrics.get("stage_timings_ms", {})
                if (
                    not isinstance(stages, dict)
                    or set(stages) - TELEMETRY_STAGES
                    or any(type(value) is not int or value < 0 for value in stages.values())
                ):
                    errors.append("telemetry stage timings are invalid")

    if kind == "error":
        error = envelope.get("error")
        required_error = {"code", "message", "retryable", "operation", "details"}
        if not isinstance(error, dict):
            errors.append("error object is required")
        elif set(error) != required_error or error.get("operation") != operation:
            errors.append("structured error fields are invalid")
    return errors


def validate_fixtures(fixtures: dict[str, Any], catalog: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if fixtures.get("schema") != "lapart.protocol-fixtures.v2":
        errors.append("unexpected fixture schema")
    for case in fixtures.get("positive", []):
        found = validate_envelope(case.get("envelope", {}), catalog, schema)
        if found:
            errors.append(f"positive fixture failed: {case.get('name')}: {'; '.join(found)}")
    for case in fixtures.get("negative", []):
        found = validate_envelope(case.get("envelope", {}), catalog, schema)
        expected = case.get("expected_error", "")
        if not any(expected in error for error in found):
            errors.append(f"negative fixture did not fail as expected: {case.get('name')}")
    return errors


def validate_source_hashes(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != "lapart.protocol-source-hashes.v1":
        errors.append("unexpected protocol source-hash schema")
    if manifest.get("algorithm") != "sha256":
        errors.append("protocol source hashes must use sha256")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return errors + ["protocol source-hash manifest is empty"]
    for relative, expected in files.items():
        path = KIT_ROOT / relative
        if not path.is_file():
            errors.append(f"hashed protocol source is missing: {relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"protocol source hash mismatch: {relative}")
    return errors


def selftest(
    catalog: dict[str, Any], schema: dict[str, Any], source_hashes: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    adapter_policy = copy.deepcopy(catalog)
    adapter_policy["adapter_owned"].append("prime_routing")
    if not validate_catalog(adapter_policy, schema):
        failures.append("adapter policy ownership mutation passed")

    missing_operation = copy.deepcopy(catalog)
    missing_operation["operations"].pop()
    if not validate_catalog(missing_operation, schema):
        failures.append("missing operation mutation passed")

    schema_drift = copy.deepcopy(schema)
    schema_drift["additionalProperties"] = True
    if not validate_catalog(catalog, schema_drift):
        failures.append("open top-level schema mutation passed")

    if negotiate_protocol([1]) != 1 or negotiate_protocol([1, 2]) != 2:
        failures.append("protocol negotiation compatibility failed")
    try:
        negotiate_protocol([3])
    except IncompatibleProtocolError:
        pass
    else:
        failures.append("incompatible protocol negotiation did not fail")

    stale_hash = copy.deepcopy(source_hashes)
    first_path = next(iter(stale_hash["files"]))
    stale_hash["files"][first_path] = "0" * 64
    if not validate_source_hashes(stale_hash):
        failures.append("stale source hash mutation passed")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_PATH)
    parser.add_argument("--source-hashes", type=Path, default=SOURCE_HASHES_PATH)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    try:
        catalog = load_json(args.catalog)
        schema = load_json(args.schema)
        fixtures = load_json(args.fixtures)
        source_hashes = load_json(args.source_hashes)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot load protocol artifact: {exc}")
        return 2

    errors = validate_catalog(catalog, schema)
    errors.extend(validate_fixtures(fixtures, catalog, schema))
    errors.extend(validate_source_hashes(source_hashes))
    if args.selftest:
        errors.extend(selftest(catalog, schema, source_hashes))
    if errors:
        print("BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: universal orchestration protocol v2")
    print(f"PASS: {len(OPERATIONS)} operations, {len(fixtures['positive'])} positive fixtures, {len(fixtures['negative'])} negative fixtures")
    if args.selftest:
        print("PASS: 6 structural, negotiation, and provenance negative cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
