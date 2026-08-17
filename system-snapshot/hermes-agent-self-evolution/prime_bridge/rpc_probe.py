"""Contained, fail-closed Prime v0.7.1 JSONL-RPC evidence probe."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
import uuid
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from prime_bridge.evidence_scan import named_credentials, prepare_evidence, scan_persisted_bytes
from prime_bridge.model_route import (
    PRIME_RUNTIME_CREDENTIAL,
    active_profile_config,
    resolve_hermes_provider_credential,
)
from prime_bridge.sandbox import ContainmentError, PodmanSandbox, admit


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "prime-rpc-evidence.schema.json"
CONTRACT_PATH = ROOT / "tests" / "fixtures" / "prime_rpc" / "prime-v0.7.1-rpc-contract.json"
CONTRACT_COMMIT = "95afd319a78ae017a41241d50b013d656a0685ce"
CONTRACT_DOCUMENT_SHA256 = "5e05a76ca16ce5b85c93870742bb4f0ed5014840585a3c5ce7e27d47e4e171b1"
DEFAULT_PROMPT = (
    "Read-only feasibility probe. Use exactly one built-in IPython tool call to evaluate 2+2. "
    "Do not access host paths, services, or networks. Reply exactly PRIME_RPC_OK:4."
)
ALLOWED_ACTIVE_EVENTS = {
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "session_action_update",
    "compaction_start",
    "compaction_end",
    "auto_retry_start",
    "auto_retry_end",
}


class ProbeProtocolError(RuntimeError):
    """The child violated the pinned RPC contract."""


class ProbeUnverified(RuntimeError):
    """A required claim was not observable from RPC evidence."""


class ProbeUnexpectedExit(RuntimeError):
    """The contained RPC process exited before completing the contract."""


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    evidence: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.evidence["status"])

    @property
    def reason(self) -> str:
        return str(self.evidence["reason"])

    @property
    def passed(self) -> bool:
        return self.status == "READY"

    @property
    def cleanup_verified(self) -> bool:
        return bool(self.evidence["containment"]["cleanup_verified"])

    @property
    def workspace_removed(self) -> bool:
        return bool(self.evidence["containment"]["container_absent"])

    @property
    def transcript(self) -> list[dict[str, Any]]:
        return list(self.evidence["transcript"])

    @property
    def final_state(self) -> dict[str, Any] | None:
        value = self.evidence.get("state")
        return value if isinstance(value, dict) else None

    @property
    def usage(self) -> dict[str, Any] | None:
        value = self.evidence.get("usage")
        return value if isinstance(value, dict) else None

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.evidence))


class LFJSONLReader:
    """Split only on LF and remove at most one immediately preceding CR."""

    def __init__(self, stream: asyncio.StreamReader, *, max_frame_bytes: int = 1_048_576):
        self._stream = stream
        self._max_frame_bytes = max_frame_bytes

    async def read(self) -> dict[str, Any]:
        try:
            raw = await self._stream.readuntil(b"\n")
        except asyncio.IncompleteReadError as error:
            if error.partial:
                raise ProbeProtocolError("unterminated_rpc_frame") from error
            raise ProbeUnexpectedExit("rpc_process_exited") from error
        except asyncio.LimitOverrunError as error:
            raise ProbeProtocolError("rpc_frame_stream_limit") from error
        if len(raw) > self._max_frame_bytes:
            raise ProbeProtocolError("rpc_frame_size_limit")
        raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        try:
            frame = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProbeProtocolError("malformed_utf8_json_frame") from error
        if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
            raise ProbeProtocolError("rpc_frame_shape")
        return frame


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contract() -> dict[str, Any]:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if value.get("commit") != CONTRACT_COMMIT or value.get("document_sha256") != CONTRACT_DOCUMENT_SHA256:
        raise ProbeProtocolError("pinned_rpc_contract_mismatch")
    return value


def _schema_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _stable_id(fields: Mapping[str, Any]) -> str:
    return _sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


def _containment_snapshot(repo: Path) -> dict[str, str]:
    lock_path = repo / "containers" / "prime-probe" / "image-lock.json"
    if not lock_path.is_file():
        seed = json.dumps({"status": "UNAVAILABLE", "reason": "image_lock_missing"}, sort_keys=True).encode()
        return {"status": "UNAVAILABLE", "upstream_sha256": _sha256(seed)}
    data = lock_path.read_bytes()
    try:
        value = json.loads(data)
    except json.JSONDecodeError:
        return {"status": "UNAVAILABLE", "upstream_sha256": _sha256(data)}
    ready = value.get("status") == "READY" and value.get("go_eligible") is True
    return {"status": "READY" if ready else "UNAVAILABLE", "upstream_sha256": _sha256(data)}


def _route_from_config(config: Mapping[str, Any]) -> dict[str, str]:
    models = config.get("models")
    if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], Mapping):
        raise ValueError("Prime configuration must project exactly one model")
    route = {
        "provider": str(config.get("provider_id", "")),
        "model": str(models[0].get("id", "")),
        "api": str(config.get("api", "")),
        "endpoint": str(config.get("baseUrl", "")),
    }
    if any(not value for value in route.values()):
        raise ValueError("Prime configuration route is incomplete")
    return route


def _runtime_credential_record(reference: str, present: bool) -> dict[str, Any]:
    availability = "VERIFIED" if present else "UNVERIFIED"
    return {
        "status": availability,
        "source": "runtime-environment",
        "runtime_reference": reference,
        "provider_id": None,
        "source_reference": reference,
        "config_fingerprint_sha256": None,
        "availability_status": availability,
        "reachability_status": "NOT_RUN",
        "value_present": present,
        "value_persisted": False,
    }


def _resolve_probe_credential(
    config: Mapping[str, Any] | None,
    explicit: str | None,
    environment: Mapping[str, str],
    hermes_config_path: Path | None,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    if explicit:
        values = named_credentials(environment, (explicit,))
        return values, _runtime_credential_record(explicit, bool(values))
    credential = config.get("apiKey") if isinstance(config, Mapping) else None
    if isinstance(credential, Mapping) and credential.get("source") == "hermes-provider-config":
        provider_id = credential.get("providerId")
        active_profile = config.get("activeProfile") if isinstance(config, Mapping) else None
        if not isinstance(provider_id, str) or not isinstance(active_profile, str):
            missing = _runtime_credential_record(PRIME_RUNTIME_CREDENTIAL, False)
            missing.update({"source": "hermes-provider-config", "provider_id": provider_id})
            return (), missing
        path = hermes_config_path or active_profile_config(active_profile)
        resolved = resolve_hermes_provider_credential(provider_id, path)
        values = (resolved.secret,) if resolved.available and resolved.secret is not None else ()
        record = resolved.public_evidence()
        record["status"] = record["availability_status"]
        return values, record
    reference = "UNSPECIFIED_CREDENTIAL"
    if isinstance(credential, Mapping) and isinstance(credential.get("environmentVariable"), str):
        reference = str(credential["environmentVariable"])
    values = named_credentials(environment, (reference,))
    return values, _runtime_credential_record(reference, bool(values))


def _with_reachability(record: Mapping[str, Any], status: str) -> dict[str, Any]:
    value = dict(record)
    value["reachability_status"] = status
    return value


def _finalize(raw: dict[str, Any], credentials: Sequence[str]) -> ProbeResult:
    prepared = prepare_evidence(raw, credentials)
    evidence = dict(prepared.value)
    evidence["scans"] = prepared.scans
    final_bytes = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    rescan = scan_persisted_bytes(final_bytes, credentials)
    if rescan["generic_scan"]["status"] != "PASS" or rescan["exact_scan"]["status"] == "FAIL":
        raise RuntimeError("evidence_secret_scan_failed")
    errors = sorted(_schema_validator().iter_errors(evidence), key=lambda item: list(item.absolute_path))
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "root"
        raise RuntimeError(f"rpc_evidence_schema_failed_at_{location}")
    return ProbeResult(evidence)


def closed_probe_evidence(
    *,
    reason_code: str,
    credential_environment: str,
    environment: Mapping[str, str],
    containment_status: str,
    upstream_sha256: str,
    expected_route: Mapping[str, str] | None = None,
    credential_record: Mapping[str, Any] | None = None,
    credential_values: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a stable, schema-valid non-authorizing record without launch."""

    credentials = tuple(credential_values) if credential_values is not None else named_credentials(environment, (credential_environment,))
    credential = dict(credential_record or _runtime_credential_record(credential_environment, bool(credentials)))
    credential_status = str(credential["availability_status"])
    route = dict(expected_route or {})
    identity = {
        "reason": reason_code,
        "credential_reference": credential["runtime_reference"],
        "credential_source": credential["source"],
        "credential_fingerprint": credential["config_fingerprint_sha256"],
        "credential_status": credential_status,
        "containment_status": containment_status,
        "upstream_sha256": upstream_sha256,
        "route": route,
        "contract": CONTRACT_DOCUMENT_SHA256,
    }
    raw = {
        "schema_version": "1.0",
        "evidence_id": _stable_id(identity),
        "status": "NOT_RUN",
        "go_eligible": False,
        "launch_started": False,
        "reason": reason_code,
        "credential": _with_reachability(credential, "NOT_RUN"),
        "containment": {
            "status": containment_status,
            "upstream_sha256": upstream_sha256,
            "cleanup_verified": False,
            "container_absent": True,
            "cgroup_populated": None,
        },
        "rpc": {
            "status": "NOT_RUN",
            "contract_commit": CONTRACT_COMMIT,
            "contract_document_sha256": CONTRACT_DOCUMENT_SHA256,
            "frame_count": 0,
            "request_count": 0,
            "agent_runs": 0,
            "ipython_calls": 0,
        },
        "route": {"status": "UNVERIFIED", **route},
        "transcript": [],
        "state": None,
        "usage": None,
        "final_text": None,
    }
    return _finalize(raw, credentials).to_dict()


def closed_probe_for_config(
    *,
    reason_code: str,
    prime_config: Mapping[str, Any] | None,
    environment: Mapping[str, str],
    containment_status: str,
    upstream_sha256: str,
    expected_route: Mapping[str, str] | None = None,
    hermes_config_path: Path | None = None,
) -> dict[str, Any]:
    """Resolve configured credential availability and emit non-secret NOT_RUN evidence."""

    credentials, record = _resolve_probe_credential(
        prime_config,
        None,
        environment,
        hermes_config_path,
    )
    return closed_probe_evidence(
        reason_code=reason_code,
        credential_environment=str(record["runtime_reference"]),
        environment=environment,
        containment_status=containment_status,
        upstream_sha256=upstream_sha256,
        expected_route=expected_route,
        credential_record=record,
        credential_values=credentials,
    )


def _model_route(model: Any) -> dict[str, str]:
    if not isinstance(model, Mapping):
        raise ProbeProtocolError("model_shape")
    endpoint = model.get("baseUrl", model.get("base_url"))
    route = {
        "provider": model.get("provider"),
        "model": model.get("id"),
        "api": model.get("api"),
        "endpoint": endpoint,
    }
    if not isinstance(route["provider"], str) or not isinstance(route["model"], str):
        raise ProbeProtocolError("model_identity_unavailable")
    if not isinstance(route["api"], str) or not isinstance(route["endpoint"], str):
        raise ProbeUnverified("model_api_or_endpoint_unobservable")
    return {key: str(value) for key, value in route.items()}


def _assert_route(model: Any, expected: Mapping[str, str]) -> dict[str, str]:
    actual = _model_route(model)
    if actual != dict(expected):
        raise ProbeProtocolError("model_route_mismatch")
    return actual


def _result_is_four(result: Any) -> bool:
    if not isinstance(result, Mapping) or not isinstance(result.get("content"), list):
        return False
    text = "\n".join(
        str(item.get("text"))
        for item in result["content"]
        if isinstance(item, Mapping) and item.get("type") == "text" and isinstance(item.get("text"), str)
    )
    return re.fullmatch(r"\s*(?:Out\[\d+\]:\s*)?4\s*", text) is not None


async def _protocol(
    process: asyncio.subprocess.Process,
    transcript: list[dict[str, Any]],
    *,
    prompt: str,
    max_frames: int,
    expected_route: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    assert process.stdin is not None and process.stdout is not None
    reader = LFJSONLReader(process.stdout)
    request_count = 0
    frame_count = 0
    phase = "PRE_PROMPT"

    async def next_frame() -> dict[str, Any]:
        nonlocal frame_count
        frame = await reader.read()
        frame_count += 1
        if frame_count > max_frames:
            raise ProbeProtocolError("rpc_frame_limit")
        transcript.append({"direction": "response", "frame": frame})
        if frame.get("type") != "response" and "id" in frame:
            raise ProbeProtocolError("event_contains_request_id")
        return frame

    async def send(command: str, **fields: Any) -> dict[str, Any]:
        nonlocal request_count
        request_count += 1
        request_id = f"phase01-{request_count}-{uuid.uuid4().hex[:8]}"
        request = {"id": request_id, "type": command, **fields}
        transcript.append({"direction": "request", "frame": request})
        process.stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        await process.stdin.drain()
        frame = await next_frame()
        if frame.get("type") != "response":
            raise ProbeProtocolError("event_preceded_command_response")
        if frame.get("id") != request_id or frame.get("command") != command:
            raise ProbeProtocolError("response_correlation_mismatch")
        if frame.get("success") is not True:
            raise ProbeProtocolError(f"command_rejected_{command}")
        return frame

    initial = (await send("get_state")).get("data")
    if not isinstance(initial, dict) or initial.get("isStreaming") is not False:
        raise ProbeProtocolError("initial_state_not_idle")
    session_id = initial.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise ProbeUnverified("session_id_unobservable")
    route = _assert_route(initial.get("model"), expected_route)

    available = (await send("get_available_models")).get("data")
    models = available.get("models") if isinstance(available, Mapping) else None
    if not isinstance(models, list):
        raise ProbeProtocolError("available_models_shape")
    matching = 0
    for model in models:
        try:
            if _model_route(model) == route:
                matching += 1
        except ProbeUnverified:
            raise
        except ProbeProtocolError:
            continue
    if matching != 1:
        raise ProbeProtocolError("available_model_identity_mismatch")

    await send("prompt", message=prompt)
    phase = "WAIT_AGENT_START"
    agent_runs = 0
    ipython_calls = 0
    tool_call_id: str | None = None

    while phase != "AGENT_ENDED":
        frame = await next_frame()
        event = frame["type"]
        if event == "response":
            raise ProbeProtocolError("uncorrelated_response_during_active_session")
        event_session = frame.get("sessionId", frame.get("activeSessionId"))
        if event_session is not None and event_session != session_id:
            raise ProbeProtocolError("event_session_substitution")
        if event == "extension_error":
            raise ProbeProtocolError("extension_error")
        if event == "agent_start":
            if phase != "WAIT_AGENT_START":
                raise ProbeProtocolError("agent_overlap_or_reorder")
            agent_runs += 1
            phase = "ACTIVE_BEFORE_TOOL"
        elif event == "tool_execution_start":
            if phase != "ACTIVE_BEFORE_TOOL" or ipython_calls != 0:
                raise ProbeProtocolError("tool_start_overlap_or_reorder")
            if frame.get("toolName") != "ipython":
                raise ProbeProtocolError("unexpected_tool_name")
            tool_call_id = frame.get("toolCallId")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ProbeProtocolError("tool_call_id_unavailable")
            args = frame.get("args")
            if not isinstance(args, Mapping) or str(args.get("code", "")).replace(" ", "") != "2+2":
                raise ProbeProtocolError("ipython_probe_code_mismatch")
            ipython_calls += 1
            phase = "TOOL_ACTIVE"
        elif event == "tool_execution_update":
            if phase != "TOOL_ACTIVE" or frame.get("toolCallId") != tool_call_id or frame.get("toolName") != "ipython":
                raise ProbeProtocolError("tool_update_correlation_mismatch")
        elif event == "tool_execution_end":
            if phase != "TOOL_ACTIVE":
                raise ProbeProtocolError("tool_end_reorder")
            if frame.get("toolCallId") != tool_call_id or frame.get("toolName") != "ipython":
                raise ProbeProtocolError("tool_end_correlation_mismatch")
            if frame.get("isError") is not False or not _result_is_four(frame.get("result")):
                raise ProbeProtocolError("ipython_result_not_exact_four")
            phase = "ACTIVE_AFTER_TOOL"
        elif event == "agent_end":
            if phase != "ACTIVE_AFTER_TOOL":
                raise ProbeProtocolError("agent_end_before_exact_tool_result")
            phase = "AGENT_ENDED"
        elif event in ALLOWED_ACTIVE_EVENTS:
            if phase not in {"ACTIVE_BEFORE_TOOL", "TOOL_ACTIVE", "ACTIVE_AFTER_TOOL"}:
                raise ProbeProtocolError("active_event_outside_agent_run")
        else:
            raise ProbeProtocolError("unexpected_rpc_event")

    final_state = (await send("get_state")).get("data")
    usage = (await send("get_session_stats")).get("data")
    last = (await send("get_last_assistant_text")).get("data")
    if not isinstance(final_state, dict) or final_state.get("isStreaming") is not False:
        raise ProbeProtocolError("final_state_not_idle")
    if final_state.get("sessionId") != session_id:
        raise ProbeProtocolError("final_session_substitution")
    _assert_route(final_state.get("model"), expected_route)
    if not isinstance(usage, dict):
        raise ProbeProtocolError("session_stats_shape")
    if usage.get("sessionId") not in (None, session_id):
        raise ProbeProtocolError("stats_session_substitution")
    final_text = last.get("text") if isinstance(last, Mapping) else None
    if final_text != "PRIME_RPC_OK:4":
        raise ProbeProtocolError("final_assistant_text_mismatch")
    receipt = {
        "status": "PASS",
        "contract_commit": CONTRACT_COMMIT,
        "contract_document_sha256": CONTRACT_DOCUMENT_SHA256,
        "frame_count": frame_count,
        "request_count": request_count,
        "agent_runs": agent_runs,
        "ipython_calls": ipython_calls,
    }
    return final_state, usage, final_text, receipt


async def _discard_stderr(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.read(8192):
        pass


async def _terminate_exec_client(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


def _clean_child_environment(credential_names: Sequence[str]) -> dict[str, str]:
    blocked = set(credential_names)
    return {key: value for key, value in os.environ.items() if key not in blocked}


def _write_prime_config(
    container_id: str,
    config: Mapping[str, Any],
    runtime_reference: str,
    *,
    child_environment: Mapping[str, str],
) -> None:
    models = config["models"]
    native = {
        "providers": {
            config["provider_id"]: {
                "baseUrl": config["baseUrl"],
                "api": config["api"],
                "apiKey": runtime_reference,
                "compat": config["compat"],
                "models": [{
                    "id": models[0]["id"],
                    "contextWindow": models[0]["contextWindow"],
                    "maxTokens": models[0]["maxTokens"],
                }],
            }
        }
    }
    code = (
        "import pathlib,sys; p=pathlib.Path('/tmp/.prime/agent/models.json'); "
        "p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o600)"
    )
    completed = subprocess.run(
        ["podman", "exec", "-i", container_id, "/usr/local/bin/python", "-c", code],
        input=json.dumps(native, separators=(",", ":")).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(child_environment),
        check=False,
    )
    if completed.returncode != 0:
        raise ContainmentError("transient_prime_config_write_failed")


async def run_probe(
    *,
    repo_root: Path = ROOT,
    prompt: str = DEFAULT_PROMPT,
    timeout_seconds: float = 600,
    max_frames: int = 512,
    cancel_event: asyncio.Event | None = None,
    environment: Mapping[str, str] | None = None,
    prime_config: Mapping[str, Any] | None = None,
    credential_environment: str | None = None,
    hermes_config_path: Path | None = None,
    expected_route: Mapping[str, str] | None = None,
    relay_socket: Path | None = None,
    fixture_source: str | None = None,
    fixture_mode: str | None = None,
) -> ProbeResult:
    """Run exactly one RPC session inside the admitted Plan 06 container."""

    if timeout_seconds <= 0 or max_frames <= 0:
        raise ValueError("timeout_seconds and max_frames must be positive")
    repo = repo_root.resolve()
    runtime_environment = dict(os.environ if environment is None else environment)
    credentials, credential_record = _resolve_probe_credential(
        prime_config,
        credential_environment,
        runtime_environment,
        hermes_config_path,
    )
    credential_reference = str(credential_record["runtime_reference"])
    route = dict(expected_route or (_route_from_config(prime_config) if prime_config is not None else {}))
    snapshot = _containment_snapshot(repo)
    _contract()

    if not credentials:
        return ProbeResult(closed_probe_evidence(
            reason_code="credential_unavailable",
            credential_environment=credential_reference,
            environment=runtime_environment,
            containment_status=snapshot["status"],
            upstream_sha256=snapshot["upstream_sha256"],
            expected_route=route,
            credential_record=credential_record,
            credential_values=credentials,
        ))
    if snapshot["status"] != "READY":
        return ProbeResult(closed_probe_evidence(
            reason_code="containment_unavailable",
            credential_environment=credential_reference,
            environment=runtime_environment,
            containment_status=snapshot["status"],
            upstream_sha256=snapshot["upstream_sha256"],
            expected_route=route,
            credential_record=credential_record,
            credential_values=credentials,
        ))
    if set(route) != {"provider", "model", "api", "endpoint"} or any(not value for value in route.values()):
        return ProbeResult(closed_probe_evidence(
            reason_code="route_unavailable",
            credential_environment=credential_reference,
            environment=runtime_environment,
            containment_status=snapshot["status"],
            upstream_sha256=snapshot["upstream_sha256"],
            expected_route=route,
            credential_record=credential_record,
            credential_values=credentials,
        ))
    if (fixture_source is None) != (fixture_mode is None):
        raise ValueError("fixture source and mode must be supplied together")

    transcript: list[dict[str, Any]] = []
    process: asyncio.subprocess.Process | None = None
    stderr_task: asyncio.Task[None] | None = None
    sandbox: PodmanSandbox | None = None
    admission = None
    launch_started = False
    final_state: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    final_text: str | None = None
    rpc_receipt = {
        "status": "FAIL",
        "contract_commit": CONTRACT_COMMIT,
        "contract_document_sha256": CONTRACT_DOCUMENT_SHA256,
        "frame_count": 0,
        "request_count": 0,
        "agent_runs": 0,
        "ipython_calls": 0,
    }
    status = "UNVERIFIED"
    reason = "rpc_not_completed"
    cleanup = {"cleanup_verified": False, "container_absent": False, "cgroup_populated": None}
    child_environment = _clean_child_environment((
        credential_reference,
        str(credential_record.get("source_reference") or ""),
        credential_environment or "",
    ))
    prime_environment = dict(child_environment)
    prime_environment[PRIME_RUNTIME_CREDENTIAL] = credentials[0]

    try:
        admission = admit(repo)
        if relay_socket is None:
            relay_hostname, relay_port = "ollama.com", 443
        else:
            parsed_endpoint = urlsplit(route["endpoint"])
            relay_hostname = str(parsed_endpoint.hostname or "")
            relay_port = parsed_endpoint.port or (443 if parsed_endpoint.scheme == "https" else 80)
            if (parsed_endpoint.scheme, relay_hostname, relay_port) not in {
                ("https", "ollama.com", 443), ("http", "127.0.0.1", 11434),
            }:
                raise ContainmentError("route endpoint is outside the exact relay allowlist")
        sandbox = PodmanSandbox(
            admission, relay_socket=relay_socket,
            relay_hostname=relay_hostname, relay_port=relay_port,
        )
        if relay_socket is None:
            sandbox_command = "import time; time.sleep(3600)"
        else:
            relay_config = {
                "listen_host": "127.0.0.1", "listen_port": relay_port,
                "uds_path": "/run/prime-relay/relay.sock",
                "allowed_hostname": relay_hostname, "allowed_port": relay_port,
            }
            sandbox_command = (
                "import asyncio,runpy; "
                "ns=runpy.run_path('/opt/phase01/relay_client.py'); "
                f"asyncio.run(ns['serve']({relay_config!r}))"
            )
        sandbox.create([sandbox_command])
        sandbox.start()
        if sandbox.container_id is None:
            raise ContainmentError("container_id_unavailable")
        if fixture_source is not None and fixture_mode is not None:
            arguments = [
                "podman", "exec", "-i", sandbox.container_id,
                "/usr/local/bin/python", "-c", fixture_source, fixture_mode,
            ]
        else:
            if prime_config is None:
                raise ProbeProtocolError("prime_config_unavailable")
            _write_prime_config(
                sandbox.container_id,
                prime_config,
                PRIME_RUNTIME_CREDENTIAL,
                child_environment=child_environment,
            )
            arguments = [
                "podman", "exec", "-i", "--env", PRIME_RUNTIME_CREDENTIAL, sandbox.container_id,
                "/opt/prime-agent/prime-agent.sh", "--dist", "--mode", "rpc", "--no-session",
                "--provider", route["provider"], "--model", route["model"],
            ]
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_environment if fixture_source is not None else prime_environment,
            start_new_session=True,
            limit=1_048_576,
        )
        launch_started = True
        stderr_task = asyncio.create_task(_discard_stderr(process.stderr))
        protocol_task = asyncio.create_task(_protocol(
            process,
            transcript,
            prompt=prompt,
            max_frames=max_frames,
            expected_route=route,
        ))
        cancel_task = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        waiters: set[asyncio.Task[Any]] = {protocol_task}
        if cancel_task is not None:
            waiters.add(cancel_task)
        done, _ = await asyncio.wait(waiters, timeout=timeout_seconds, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task is not None and cancel_task in done:
            reason = "rpc_cancelled"
            if process.stdin is not None and not process.stdin.is_closing():
                abort = {"id": f"phase01-abort-{uuid.uuid4().hex[:8]}", "type": "abort"}
                transcript.append({"direction": "request", "frame": abort})
                process.stdin.write(json.dumps(abort, separators=(",", ":")).encode() + b"\n")
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await process.stdin.drain()
            protocol_task.cancel()
        elif protocol_task in done:
            try:
                final_state, usage, final_text, rpc_receipt = protocol_task.result()
                status = "READY"
                reason = "contained_rpc_contract_satisfied"
            except ProbeUnverified:
                reason = "rpc_claim_unobservable"
            except ProbeProtocolError:
                reason = "rpc_contract_violation"
            except ProbeUnexpectedExit:
                reason = "rpc_process_exited"
        else:
            reason = "rpc_timeout"
            protocol_task.cancel()
        if cancel_task is not None:
            cancel_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, ProbeProtocolError, ProbeUnverified, ProbeUnexpectedExit):
            await protocol_task
    except (ContainmentError, FileNotFoundError):
        reason = "containment_admission_or_launch_failed"
    finally:
        if process is not None:
            await _terminate_exec_client(process)
        if stderr_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
        if sandbox is not None:
            try:
                cleanup = sandbox.teardown()
            except ContainmentError:
                cleanup = {"cleanup_verified": False, "container_absent": False, "cgroup_populated": None}
                status = "UNVERIFIED"
                reason = "containment_teardown_failed"
        if admission is not None:
            admission.close()

    if not cleanup["cleanup_verified"]:
        status = "UNVERIFIED"
    raw = {
        "schema_version": "1.0",
        "evidence_id": _stable_id({
            "contract": CONTRACT_DOCUMENT_SHA256,
            "route": route,
            "upstream": snapshot["upstream_sha256"],
            "transcript": transcript,
        }),
        "status": status,
        "go_eligible": status == "READY" and cleanup["cleanup_verified"],
        "launch_started": launch_started,
        "reason": reason,
        "credential": _with_reachability(
            credential_record,
            "VERIFIED" if status == "READY" else ("UNVERIFIED" if launch_started else "NOT_RUN"),
        ),
        "containment": {
            "status": "READY",
            "upstream_sha256": snapshot["upstream_sha256"],
            **cleanup,
        },
        "rpc": rpc_receipt,
        "route": {"status": "VERIFIED" if status == "READY" else "UNVERIFIED", **route},
        "transcript": transcript,
        "state": final_state,
        "usage": usage,
        "final_text": final_text,
    }
    return _finalize(raw, credentials)


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Run the contained Prime v0.7.1 JSONL-RPC probe")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--prime-config", type=Path)
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--relay-socket", type=Path)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    if args.prime_config is None:
        config = None
        route: dict[str, str] = {}
    else:
        config = json.loads(args.prime_config.read_text(encoding="utf-8"))
        route = _route_from_config(config)
    if not args.live or os.environ.get("PRIME_LIVE_PROBE") != "1":
        snapshot = _containment_snapshot(ROOT)
        evidence = closed_probe_for_config(
            reason_code="live_opt_in_absent",
            prime_config=config,
            environment=os.environ,
            containment_status=snapshot["status"],
            upstream_sha256=snapshot["upstream_sha256"],
            expected_route=route,
        )
        print(json.dumps(evidence, sort_keys=True))
        return 2

    result = asyncio.run(run_probe(
        prime_config=config,
        relay_socket=args.relay_socket,
        timeout_seconds=args.timeout,
    ))
    payload = json.dumps(result.to_dict(), sort_keys=True, indent=2)
    if args.transcript is not None:
        args.transcript.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(_cli())
