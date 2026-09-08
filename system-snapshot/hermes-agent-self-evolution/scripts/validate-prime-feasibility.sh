#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
bash "$SCRIPT_DIR/sync-phase01-tools-env.sh" --check >/dev/null
PYTHON="$(bash "$SCRIPT_DIR/sync-phase01-tools-env.sh" --python)"
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" - "$REPO_ROOT" "$@" <<'PY'
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import secrets
from urllib.parse import urlsplit
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

root = Path(sys.argv[1])
sys.path.insert(0, str(root))

from prime_bridge.evidence_scan import scan_persisted_bytes
from prime_bridge.attestation import consumed_evidence_matches
from prime_bridge.inference_relay import InferenceRelay, RelayPolicy
from prime_bridge.model_route import resolve_model_route
from prime_bridge.rpc_probe import closed_probe_evidence, closed_probe_for_config, run_probe


UTC = dt.timezone.utc
COMPONENT_NAMES = (
    "provenance", "route", "image", "containment", "rpc", "scans",
    "native_before", "native_after", "context_preservation", "operator_authorization", "desktop",
)
REQUIRED_COMPONENT_NAMES = tuple(name for name in COMPONENT_NAMES if name != "desktop")
PREREQUISITE_PATH = root / ".planning/phases/01-contracts-feasibility/evidence/phase01-podman-prerequisite.json"
PROVENANCE_PATH = root / ".planning/phases/01-contracts-feasibility/evidence/prime-agent-provenance.json"
IMAGE_PATH = root / "containers/prime-probe/image-lock.json"
SOURCE_LOCK_PATH = root / "containers/prime-probe/prime-source.lock.json"
DESKTOP_PATH = root / ".git/phase01-gap-lifecycle/live-desktop-evidence.json"
OPERATOR_PATH = root / ".planning/phases/01-contracts-feasibility/evidence/phase01-operator-attestation.json"
DESKTOP_STORE = root / ".git/phase01-gap-lifecycle/desktop-attestation-nonces.sqlite3"
TRANSCRIPT_PATH = root / ".planning/phases/01-contracts-feasibility/evidence/phase01-live-rpc-transcript.jsonl"


class FeasibilityError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    data = path.read_bytes()
    value = json.loads(data)
    if not isinstance(value, dict):
        raise FeasibilityError(f"object required: {path}")
    return value, data


def validate(path: Path, schema_path: Path) -> tuple[dict[str, Any], bytes]:
    value, data = read_json(path)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "root"
        raise FeasibilityError(f"schema failure at {location}: {errors[0].message}")
    return value, data


def component(status: str, source: str, data: bytes, upstream: str = "") -> dict[str, Any]:
    return {"status": status, "source_kind": source, "sha256": digest(data), "upstream_sha256": upstream}


def capture_baseline() -> tuple[dict[str, Any], bytes]:
    import subprocess
    completed = subprocess.run(
        ["bash", str(root / "scripts/capture-prime-integration-baseline.sh"), "capture"],
        cwd=root, capture_output=True, check=False,
    )
    if completed.returncode != 0:
        raise FeasibilityError("native baseline capture failed")
    value = json.loads(completed.stdout)
    schema = json.loads((root / "contracts/hermes-baseline-evidence.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)
    return value, completed.stdout


def normalized_baseline(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"captured_at", "run_nonce"}}


def aggregate_test_bundle(bundle_path: Path) -> dict[str, Any]:
    bundle, raw = read_json(bundle_path)
    required = {"mode", "prerequisite", "components", "scan_summary", "launch", "context"}
    if set(bundle) != required or set(bundle["components"]) != set(COMPONENT_NAMES):
        raise FeasibilityError("closed test bundle contract violated")
    prerequisite = bundle["prerequisite"]
    if set(prerequisite) != {"status", "go_eligible", "sha256"}:
        raise FeasibilityError("closed prerequisite bundle violated")
    components: dict[str, Any] = {}
    for name in COMPONENT_NAMES:
        item = bundle["components"][name]
        if set(item) != {"status", "sha256", "upstream_sha256"}:
            raise FeasibilityError(f"closed component bundle violated: {name}")
        components[name] = {**item, "source_kind": "SIMULATED"}
    reasons = ["fixture_evidence_is_non_authorizing"]
    if bundle["mode"] == "unavailable":
        if prerequisite["status"] != "UNAVAILABLE" or prerequisite["go_eligible"] is not False:
            raise FeasibilityError("unavailable prerequisite shape mismatch")
        for name in ("image", "containment", "rpc"):
            if components[name]["status"] != "NOT_RUN" or components[name]["upstream_sha256"] != prerequisite["sha256"]:
                raise FeasibilityError("mixed READY/NOT_RUN unavailable chain")
        if any(bundle["launch"].values()):
            raise FeasibilityError("unavailable chain claims a launch")
        reasons.append("podman_prerequisite_unavailable")
    elif bundle["mode"] not in {"simulated", "live"}:
        raise FeasibilityError("unknown test mode")
    if components["native_before"]["sha256"] != components["native_after"]["sha256"]:
        components["context_preservation"]["status"] = "FAIL"
        reasons.append("native_context_drift")
    return {
        "schema_version": "1.0", "report_id": uuid.uuid4().hex,
        "captured_at": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_kind": "SIMULATED", "status": "UNVERIFIED", "go_eligible": False,
        "decision_options": ["NO_GO"], "human_decision": None,
        "prerequisite": prerequisite, "components": components,
        "scan_summary": bundle["scan_summary"], "launch": bundle["launch"],
        "context": bundle["context"], "reasons": reasons,
    }


async def aggregate_live(route_path: Path, live: bool, timeout: float, requested_report_id: str | None) -> dict[str, Any]:
    report_id = requested_report_id or uuid.uuid4().hex
    if len(report_id) != 32 or any(char not in "0123456789abcdef" for char in report_id):
        raise FeasibilityError("report id must be 32 lowercase hexadecimal characters")
    reasons: list[str] = []
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    components: dict[str, Any] = {}
    launch = {"podman_build_started": False, "podman_run_started": False, "prime_child_started": False}
    route_context = {"provider": "", "model": "", "api": "", "endpoint": ""}
    image_digest = ""
    prerequisite_hash = "0" * 64
    desktop_report_id = ""
    operator_report_id = ""
    scan_summary = {"generic_status": "PASS", "exact_status": "NOT_APPLICABLE", "credential_values_supplied": False}
    try:
        before, before_raw = capture_baseline()
        components["native_before"] = component("PASS" if before["status"] == "READY" else "UNVERIFIED", "LIVE", before_raw)

        prerequisite, prerequisite_raw = validate(PREREQUISITE_PATH, root / "contracts/phase01-podman-prerequisite.schema.json")
        prerequisite_hash = digest(prerequisite_raw)
        prerequisite_summary = {"status": prerequisite["status"], "go_eligible": prerequisite["go_eligible"], "sha256": prerequisite_hash}

        provenance, provenance_raw = read_json(PROVENANCE_PATH)
        source_lock, source_lock_raw = read_json(SOURCE_LOCK_PATH)
        provenance_ok = digest(provenance_raw) == source_lock.get("provenance_sha256")
        components["provenance"] = component("PASS" if provenance_ok else "FAIL", "LIVE", provenance_raw, digest(source_lock_raw))

        route_policy, route_raw = read_json(route_path)
        route = resolve_model_route(route_policy)
        if route.ready:
            config = route.prime_config
            route_context = {
                "provider": config["provider_id"], "model": config["models"][0]["id"],
                "api": config["api"], "endpoint": config["baseUrl"],
            }
            components["route"] = component("PASS", "LIVE", route_raw)
        else:
            config = None
            components["route"] = component("FAIL", "LIVE", route_raw)
            reasons.append("model_route_unavailable")

        if prerequisite["status"] == "UNAVAILABLE":
            unavailable_seed = json.dumps({"status": "NOT_RUN", "upstream_sha256": prerequisite_hash}, sort_keys=True).encode()
            for name in ("image", "containment", "rpc"):
                components[name] = component("NOT_RUN", "LIVE", unavailable_seed, prerequisite_hash)
            closed = closed_probe_evidence(
                reason_code="podman_prerequisite_unavailable", credential_environment="UNSPECIFIED_CREDENTIAL",
                environment={}, containment_status="UNAVAILABLE", upstream_sha256=prerequisite_hash,
            )
            scan_raw = json.dumps(closed["scans"], sort_keys=True).encode()
            components["scans"] = component("PASS", "LIVE", scan_raw, prerequisite_hash)
            scan_summary = {
                "generic_status": closed["scans"]["generic_scan"]["status"],
                "exact_status": closed["scans"]["exact_scan"]["status"],
                "credential_values_supplied": bool(closed["scans"]["exact_scan"]["values_supplied"]),
            }
            reasons.append("podman_prerequisite_unavailable")
        else:
            image, image_raw = read_json(IMAGE_PATH)
            image_ok = image.get("status") == "READY" and image.get("go_eligible") is True and image.get("prerequisite_sha256") == prerequisite_hash
            image_digest = str(image.get("runnable_image", ""))
            components["image"] = component("PASS" if image_ok else "FAIL", "LIVE", image_raw, prerequisite_hash)
            if live and os.environ.get("PRIME_LIVE_PROBE") == "1" and config is not None:
                endpoint = urlsplit(route_context["endpoint"])
                if (endpoint.scheme, endpoint.hostname, endpoint.port or (443 if endpoint.scheme == "https" else 80)) == ("https", "ollama.com", 443):
                    relay_policy = RelayPolicy.resolve("ollama.com", 443)
                elif (endpoint.scheme, endpoint.hostname, endpoint.port) == ("http", "127.0.0.1", 11434):
                    relay_policy = RelayPolicy("127.0.0.1", ("127.0.0.1",), 11434)
                else:
                    raise FeasibilityError("route endpoint is outside the exact relay allowlist")
                probe_environment = dict(os.environ)
                if config.get("apiKey") == {"source": "runtime-environment", "environmentVariable": "HERMES_PRIME_LOCAL_PLACEHOLDER"}:
                    probe_environment["HERMES_PRIME_LOCAL_PLACEHOLDER"] = secrets.token_urlsafe(32)
                with tempfile.TemporaryDirectory(prefix="phase01-relay-", dir="/tmp") as relay_directory:
                    relay_path = Path(relay_directory) / "relay.sock"
                    async with InferenceRelay(relay_path, relay_policy):
                        probe = (await run_probe(
                            prime_config=config, expected_route=route_context,
                            relay_socket=relay_path, timeout_seconds=timeout,
                            environment=probe_environment,
                        )).to_dict()
                transcript_bytes = b"".join(
                    json.dumps(item, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                    for item in probe["transcript"]
                )
                if scan_persisted_bytes(transcript_bytes, ())['generic_scan']['status'] != 'PASS':
                    raise FeasibilityError("transcript generic scan failed")
                TRANSCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
                TRANSCRIPT_PATH.write_bytes(transcript_bytes)
            else:
                probe = closed_probe_for_config(
                    reason_code="live_opt_in_absent", prime_config=config, environment=os.environ,
                    containment_status="READY" if image_ok else "UNAVAILABLE", upstream_sha256=digest(image_raw), expected_route=route_context,
                )
            probe_raw = json.dumps(probe, sort_keys=True, separators=(",", ":")).encode()
            launch["podman_run_started"] = bool(probe["launch_started"])
            launch["prime_child_started"] = bool(probe["launch_started"])
            containment_ok = probe["containment"]["status"] == "READY" and probe["containment"]["cleanup_verified"] is True and probe["containment"]["container_absent"] is True and probe["containment"]["cgroup_populated"] == 0
            rpc_ok = probe["status"] == "READY" and probe["go_eligible"] is True and probe["rpc"]["status"] == "PASS" and probe["rpc"]["ipython_calls"] == 1 and probe["final_text"] == "PRIME_RPC_OK:4" and probe["route"] == {"status": "VERIFIED", **route_context}
            scan_ok = probe["scans"]["generic_scan"]["status"] == "PASS" and probe["scans"]["exact_scan"]["status"] == "PASS"
            scan_summary = {
                "generic_status": probe["scans"]["generic_scan"]["status"],
                "exact_status": probe["scans"]["exact_scan"]["status"],
                "credential_values_supplied": bool(probe["scans"]["exact_scan"]["values_supplied"]),
            }
            components["containment"] = component("PASS" if containment_ok else ("NOT_RUN" if not probe["launch_started"] else "FAIL"), "LIVE", probe_raw, digest(image_raw))
            components["rpc"] = component("PASS" if rpc_ok else ("NOT_RUN" if probe["status"] == "NOT_RUN" else "FAIL"), "LIVE", probe_raw, digest(image_raw))
            components["scans"] = component("PASS" if scan_ok else "UNVERIFIED", "LIVE", json.dumps(probe["scans"], sort_keys=True).encode(), digest(probe_raw))
            if not rpc_ok:
                reasons.append(str(probe["reason"]))

        if OPERATOR_PATH.is_file():
            operator, operator_raw = validate(OPERATOR_PATH, root / "contracts/remote-client-evidence.schema.json")
            operator_report_id = operator["report_id"]
            operator_ok = (
                operator["status"] == "READY" and operator["go_eligible"] is True
                and operator_report_id == report_id
                and operator["client_id"] == "stack-host"
                and operator["endpoint"] == "http://127.0.0.1:8642"
                and consumed_evidence_matches(DESKTOP_STORE, operator)
            )
            components["operator_authorization"] = component("PASS" if operator_ok else "FAIL", "LIVE", operator_raw)
            if not operator_ok:
                reasons.append("operator_authorization_invalid")
        else:
            seed = json.dumps({"status": "UNVERIFIED", "reason": "operator_authorization_unavailable"}, sort_keys=True).encode()
            components["operator_authorization"] = component("UNVERIFIED", "LIVE", seed)
            reasons.append("operator_authorization_unavailable")

        if DESKTOP_PATH.is_file():
            desktop, desktop_raw = validate(DESKTOP_PATH, root / "contracts/remote-client-evidence.schema.json")
            desktop_report_id = desktop["report_id"]
            desktop_ok = (
                desktop["status"] == "READY" and desktop["go_eligible"] is True
                and desktop_report_id == report_id
                and desktop["client_id"] == "thin-client"
                and desktop["endpoint"] == "http://__TAILSCALE_IPV4__:8642"
                and consumed_evidence_matches(DESKTOP_STORE, desktop)
            )
            components["desktop"] = component("PASS" if desktop_ok else "FAIL", "LIVE", desktop_raw)
            if not desktop_ok:
                reasons.append("desktop_evidence_invalid")
        else:
            seed = json.dumps({"status": "UNVERIFIED", "reason": "desktop_evidence_deferred"}, sort_keys=True).encode()
            components["desktop"] = component("UNVERIFIED", "LIVE", seed)
            reasons.append("desktop_evidence_deferred")
    except Exception as error:
        reasons.append(f"aggregation_error:{type(error).__name__}")
        prerequisite_summary = {"status": "READY", "go_eligible": False, "sha256": prerequisite_hash}
    finally:
        try:
            after, after_raw = capture_baseline()
            components["native_after"] = component("PASS" if after["status"] == "READY" else "UNVERIFIED", "LIVE", after_raw)
        except Exception as error:
            reasons.append(f"after_capture_error:{type(error).__name__}")
            unavailable = json.dumps({"status": "UNVERIFIED", "reason": "after_capture_failed"}, sort_keys=True).encode()
            components["native_after"] = component("UNVERIFIED", "LIVE", unavailable)

    before_fingerprint = before.get("canonical_fingerprint_sha256", "0" * 64) if before else "0" * 64
    after_fingerprint = after.get("canonical_fingerprint_sha256", "0" * 64) if after else "0" * 64
    preservation_ok = before is not None and after is not None and normalized_baseline(before) == normalized_baseline(after)
    preservation_raw = json.dumps({"before": before_fingerprint, "after": after_fingerprint}, sort_keys=True).encode()
    components["context_preservation"] = component("PASS" if preservation_ok else "FAIL", "LIVE", preservation_raw)
    if not preservation_ok:
        reasons.append("native_context_drift")
    missing_seed = json.dumps({"status": "UNVERIFIED", "reason": "component_unavailable"}, sort_keys=True).encode()
    for name in COMPONENT_NAMES:
        components.setdefault(name, component("UNVERIFIED", "LIVE", missing_seed))
    if components["desktop"]["status"] == "UNVERIFIED":
        desktop_report_id = ""
        reasons.append("desktop_evidence_deferred")
    prerequisite_summary = locals().get("prerequisite_summary", {"status": "READY", "go_eligible": False, "sha256": prerequisite_hash})
    all_pass = all(components[name]["status"] == "PASS" for name in REQUIRED_COMPONENT_NAMES)
    if DESKTOP_PATH.is_file():
        all_pass = all_pass and components["desktop"]["status"] == "PASS"
    status = (
        "UNVERIFIED" if prerequisite_summary["status"] == "UNAVAILABLE"
        else ("READY" if all_pass else ("FAIL" if any(item["status"] == "FAIL" for item in components.values()) else "UNVERIFIED"))
    )
    report = {
        "schema_version": "1.0", "report_id": report_id,
        "captured_at": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_kind": "LIVE", "status": status, "go_eligible": all_pass,
        "decision_options": ["GO", "NO_GO"] if all_pass else ["NO_GO"], "human_decision": None,
        "prerequisite": prerequisite_summary, "components": components, "scan_summary": scan_summary, "launch": launch,
        "context": {**route_context, "image_digest": image_digest, "prerequisite_sha256": prerequisite_hash,
                    "native_before_fingerprint": before_fingerprint, "native_after_fingerprint": after_fingerprint,
                    "operator_report_id": operator_report_id, "desktop_report_id": desktop_report_id},
        "reasons": sorted(set(reasons)),
    }
    generic = scan_persisted_bytes(json.dumps(report, sort_keys=True).encode(), ())
    if generic["generic_scan"]["status"] != "PASS":
        report["status"] = "FAIL"; report["go_eligible"] = False; report["decision_options"] = ["NO_GO"]
        report["reasons"] = sorted(set(report["reasons"] + ["generic_secret_scan_failed"]))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Conjunctive Prime/Hermes feasibility aggregator")
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--report-id")
    parser.add_argument("--test-bundle", type=Path)
    args = parser.parse_args(sys.argv[2:])
    try:
        if args.test_bundle:
            if os.environ.get("PRIME_FEASIBILITY_TEST_MODE") != "1":
                raise FeasibilityError("test bundle rejected outside test mode")
            report = aggregate_test_bundle(args.test_bundle)
        else:
            report = asyncio.run(aggregate_live(args.route, args.live, args.timeout, args.report_id))
        schema = json.loads((root / "contracts/prime-feasibility-report.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(report)
    except Exception as error:
        print(f"prime feasibility: FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["go_eligible"] else 2


raise SystemExit(main())
PY
