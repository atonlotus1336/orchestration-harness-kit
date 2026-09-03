#!/usr/bin/env python3.11
"""
RAG Orchestrator MCP Server
===========================
Single unified MCP server over GraphRAG + Memoryweaver.
Add new backends by subclassing RAGBackend and calling registry.register().
Runs as stdio MCP server and also exposes a small CLI status probe for audits.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

CLI_STATUS_MODE = "--status-json" in sys.argv[1:]
if not CLI_STATUS_MODE:
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
else:
    types = None
    Server = None
    stdio_server = None

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    DEFAULT_MEMORYWEAVER_DATA_DIR = r"D:\MyVault\.claude\memory\memoryweaver"
    DEFAULT_MEMORYWEAVER_FTS_DB_PATH = r"D:\MyVault\.claude\memory\memoryweaver\fts.db"
    MEMORYWEAVER_SOURCE_DIR = r"D:\MyVault\.claude\memoryweaver"
    GRAPHRAG_ROOT = r"D:\MyVault\.rag\graphrag"
    MANUAL_REMEDIATION_SCRIPT = r"D:\MyVault\.mcp-servers\remediation\Invoke-MyVaultBackendRemediation.ps1"
    MANUAL_REMEDIATION_SCRIPT_COMMAND_PATH = MANUAL_REMEDIATION_SCRIPT
    PROJECT_VAULT_ROOT = r"D:\MyVault"
else:
    DEFAULT_MEMORYWEAVER_DATA_DIR = "/mnt/d/MyVault/.claude/memory/memoryweaver"
    DEFAULT_MEMORYWEAVER_FTS_DB_PATH = "/home/alice/.local/share/memoryweaver-runtime/fts.db"
    MEMORYWEAVER_SOURCE_DIR = "/mnt/d/MyVault/.claude/memoryweaver"
    GRAPHRAG_ROOT = "/mnt/d/MyVault/.rag/graphrag"
    MANUAL_REMEDIATION_SCRIPT = "/mnt/d/MyVault/.mcp-servers/remediation/Invoke-MyVaultBackendRemediation.ps1"
    MANUAL_REMEDIATION_SCRIPT_COMMAND_PATH = r"D:\MyVault\.mcp-servers\remediation\Invoke-MyVaultBackendRemediation.ps1"
    PROJECT_VAULT_ROOT = "/mnt/d/MyVault"

GRAPHRAG_OUTPUT_DIR = f"{GRAPHRAG_ROOT}/output"
WINDOWS_USER = os.environ.get("WINDOWS_USER") or os.environ.get("USERNAME")
DEFAULT_GRAPHRAG_WINDOWS_PYTHON = (
    rf"C:\Users\{WINDOWS_USER}\AppData\Roaming\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe"
    if IS_WINDOWS and WINDOWS_USER
    else (
        f"/mnt/c/Users/{WINDOWS_USER}/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe"
        if WINDOWS_USER
        else "python.exe"
    )
)
GRAPHRAG_WINDOWS_PYTHON = os.environ.get("GRAPHRAG_WINDOWS_PYTHON") or (
    DEFAULT_GRAPHRAG_WINDOWS_PYTHON
)
GRAPHRAG_WINDOWS_SITE_PACKAGES = r"D:\MyVault\.rag\systems\.venv\Lib\site-packages"
GRAPHRAG_WINDOWS_ROOT = r"D:\MyVault\.rag\graphrag"
GRAPHRAG_REQUIRED_OUTPUTS = [
    "communities.parquet",
    "community_reports.parquet",
    "entities.parquet",
    "relationships.parquet",
    "text_units.parquet",
]
ORCHESTRATION_CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "orchestration-contracts"
REQUEST_CONTEXT_SCHEMA_PATH = ORCHESTRATION_CONTRACTS_DIR / "request-context.schema.json"
GATEWAY_STATUS_SCHEMA_PATH = ORCHESTRATION_CONTRACTS_DIR / "gateway-status.schema.json"
GATEWAY_REGISTRY_PATH = ORCHESTRATION_CONTRACTS_DIR / "gateway-registry.json"
BACKEND_POLICY_PATH = ORCHESTRATION_CONTRACTS_DIR / "backend-policy.json"
BACKEND_REMEDIATION_PATH = ORCHESTRATION_CONTRACTS_DIR / "backend-remediation.json"
REQUEST_CONTEXT_REQUIRED_FIELDS = (
    "request_id",
    "session_id",
    "harness",
    "profile",
    "project",
    "workspace_root",
    "intent",
    "selected_backend",
)
RECOVERY_STATE: dict[str, dict[str, Any]] = {}
RECOVERY_TASKS: dict[str, asyncio.Task[Any]] = {}

# Set memoryweaver data dir and Linux-ext4 FTS DB path before importing.
os.environ["MEMORYWEAVER_DATA_DIR"] = os.environ.get(
    "MEMORYWEAVER_DATA_DIR", DEFAULT_MEMORYWEAVER_DATA_DIR
)
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.environ.get(
    "MEMORYWEAVER_FTS_DB_PATH", DEFAULT_MEMORYWEAVER_FTS_DB_PATH
)
if MEMORYWEAVER_SOURCE_DIR not in sys.path:
    sys.path.insert(0, MEMORYWEAVER_SOURCE_DIR)


BACKEND_RETRY_ATTEMPTS = int(os.environ.get("RAG_BACKEND_RETRY_ATTEMPTS", "2"))
BACKEND_RETRY_DELAY_SECONDS = float(os.environ.get("RAG_BACKEND_RETRY_DELAY_SECONDS", "0.75"))


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _isoformat(value: dt.datetime | None = None) -> str:
    return (value or _utcnow()).isoformat()


def _load_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_registry_index() -> dict[str, dict[str, Any]]:
    raw = _load_json_file(GATEWAY_REGISTRY_PATH, {"backends": []})
    entries = raw.get("backends", [])
    index: dict[str, dict[str, Any]] = {}
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                index[str(entry["id"])] = entry
    return index


REQUEST_CONTEXT_SCHEMA = _load_json_file(REQUEST_CONTEXT_SCHEMA_PATH, {})
GATEWAY_STATUS_SCHEMA = _load_json_file(GATEWAY_STATUS_SCHEMA_PATH, {})
GATEWAY_REGISTRY_RAW = _load_json_file(GATEWAY_REGISTRY_PATH, {"backends": []})
GATEWAY_REGISTRY_INDEX = _load_registry_index()
BACKEND_POLICY = _load_json_file(BACKEND_POLICY_PATH, {"backends": {}})
BACKEND_REMEDIATION = _load_json_file(BACKEND_REMEDIATION_PATH, {"backends": {}})


def _policy_for(backend_name: str) -> dict[str, Any]:
    defaults = {
        "retry_attempts": int(BACKEND_POLICY.get("default_retry_attempts", BACKEND_RETRY_ATTEMPTS)),
        "retry_delay_seconds": float(
            BACKEND_POLICY.get("default_retry_delay_seconds", BACKEND_RETRY_DELAY_SECONDS)
        ),
        "status_timeout_seconds": 2.0,
        "query_timeout_seconds": 20.0,
        "fallback_backends": [],
        "circuit_failure_threshold": int(BACKEND_POLICY.get("default_circuit_failure_threshold", 3)),
        "circuit_cooldown_seconds": float(
            BACKEND_POLICY.get("default_circuit_cooldown_seconds", 300.0)
        ),
        "auto_recovery_allowed": False,
        "auto_recovery_actions": [],
        "max_recovery_attempts": 0,
        "recovery_backoff_seconds": 0.0,
        "eligible_error_classes": [],
        "post_recovery_probe": None,
    }
    backend_overrides = (BACKEND_POLICY.get("backends") or {}).get(backend_name, {})
    if isinstance(backend_overrides, dict):
        defaults.update(backend_overrides)
    return defaults


def _registry_entry(backend_name: str) -> dict[str, Any]:
    return GATEWAY_REGISTRY_INDEX.get(backend_name, {})


def _manual_remediation_entries(backend_name: str) -> list[dict[str, Any]]:
    entries = (BACKEND_REMEDIATION.get("backends") or {}).get(backend_name, [])
    return [entry for entry in entries if isinstance(entry, dict)]


def _manual_remediation_command(
    backend_name: str,
    remediation_id: str,
    *,
    dry_run: bool = False,
) -> str:
    base = (
        f"powershell -ExecutionPolicy Bypass -File {MANUAL_REMEDIATION_SCRIPT_COMMAND_PATH}"
        if IS_WINDOWS
        else f"powershell.exe -ExecutionPolicy Bypass -File {MANUAL_REMEDIATION_SCRIPT_COMMAND_PATH}"
    )
    command = f'{base} -Backend "{backend_name}" -RemediationId "{remediation_id}"'
    if dry_run:
        command += " -DryRun"
    return command


def _manual_remediation_summary(backend_name: str) -> dict[str, Any] | None:
    entries = _manual_remediation_entries(backend_name)
    if not entries:
        return None
    entry = entries[0]
    remediation_id = str(entry.get("id", ""))
    return {
        "id": remediation_id,
        "description": entry.get("description"),
        "dry_run_supported": bool(entry.get("dry_run_supported")),
        "requires_auto_recovery_exhausted": bool(entry.get("requires_auto_recovery_exhausted")),
        "command": _manual_remediation_command(backend_name, remediation_id, dry_run=False),
        "dry_run_command": _manual_remediation_command(backend_name, remediation_id, dry_run=True),
        "notes": entry.get("notes", []),
    }


def _detect_harness() -> str:
    override = os.environ.get("MyVault_HARNESS")
    if override:
        return override.lower()
    combined = " ".join(
        [
            os.environ.get("TERM_PROGRAM", ""),
            os.environ.get("WT_SESSION", ""),
            os.environ.get("COMSPEC", ""),
            str(Path.cwd()),
            " ".join(sys.argv),
        ]
    ).lower()
    if "codex" in combined:
        return "codex"
    if "claude" in combined:
        return "claude"
    if "hermes" in combined:
        return "hermes"
    return "unknown"


def _default_project_name(workspace_root: str) -> str:
    lowered = workspace_root.lower()
    if "MyVault" in lowered:
        return "MyVault"
    return Path(workspace_root).name or "unknown-project"


def _build_request_context(
    arguments: dict[str, Any] | None = None,
    *,
    intent_override: str | None = None,
    selected_backend: str | None = None,
) -> dict[str, Any]:
    arguments = arguments or {}
    raw_context = arguments.get("context")
    context = dict(raw_context) if isinstance(raw_context, dict) else {}
    workspace_root = (
        str(context.get("workspace_root"))
        if context.get("workspace_root")
        else os.environ.get("MyVault_WORKSPACE_ROOT", PROJECT_VAULT_ROOT)
    )
    backend_selection = (
        selected_backend
        or context.get("selected_backend")
        or arguments.get("backend")
        or arguments.get("preferred_backend")
        or "all"
    )
    intent = intent_override or context.get("intent") or arguments.get("intent") or "query"

    context.setdefault("request_id", uuid.uuid4().hex)
    context.setdefault("session_id", os.environ.get("MyVault_SESSION_ID", "unknown-session"))
    context.setdefault("harness", _detect_harness())
    context.setdefault("profile", os.environ.get("MyVault_PROFILE", "default"))
    context.setdefault("project", _default_project_name(workspace_root))
    context.setdefault("workspace_root", workspace_root)
    context["intent"] = intent
    context["selected_backend"] = backend_selection
    return context


def _validate_request_context(context: dict[str, Any]) -> list[str]:
    missing = [field for field in REQUEST_CONTEXT_REQUIRED_FIELDS if not context.get(field)]
    return sorted(missing)


def _classify_error_message(message: str | None, payload: Any | None = None) -> str | None:
    if not message and payload is not None:
        try:
            message = json.dumps(payload)
        except Exception:
            message = str(payload)
    if not message:
        return None

    lowered = str(message).lower()
    if any(token in lowered for token in ("401", "403", "permission", "approval", "unauthorized", "forbidden")):
        return "auth_or_approval_error"
    if any(token in lowered for token in ("schema", "json", "parse", "invalid response", "422", "400", "protocol")):
        return "protocol_error"
    if any(token in lowered for token in ("fts", "index", "parquet", "sqlite", "db", "database", "missing output")):
        return "data_error"
    if any(
        token in lowered
        for token in (
            "timed out",
            "timeout",
            "connection refused",
            "unreachable",
            "not reachable",
            "all connection attempts failed",
            "no route to host",
            "service is not reachable",
        )
    ):
        return "backend_error"
    if any(token in lowered for token in ("circuit breaker", "circuit open", "gateway", "mcp")):
        return "gateway_error"
    return "unknown"


def _is_usable_result(item: dict[str, Any]) -> bool:
    text = str(item.get("text", ""))
    return item.get("score", 0) > 0 or not text.startswith("Error:")


def _results_usable(results: list[dict[str, Any]]) -> bool:
    return any(_is_usable_result(item) for item in results)


def _results_error_text(results: list[dict[str, Any]]) -> str | None:
    if not results:
        return "Backend returned no results"
    if _results_usable(results):
        return None
    for item in results:
        text = str(item.get("text", ""))
        if text.startswith("Error:"):
            return text
    return "Backend returned only unusable results"


def _annotate_results(
    results: list[dict[str, Any]],
    *,
    backend_name: str,
    attempt: int,
    request_context: dict[str, Any],
    degraded: bool = False,
    fallback_source: str | None = None,
) -> list[dict[str, Any]]:
    for item in results:
        metadata = item.setdefault("metadata", {})
        metadata.setdefault("attempts", attempt)
        metadata.setdefault("request_id", request_context["request_id"])
        metadata.setdefault("session_id", request_context["session_id"])
        if degraded:
            metadata["degraded_fallback"] = True
        if fallback_source:
            metadata["fallback_source"] = fallback_source
        item.setdefault("backend", backend_name)
    return results


def _backend_runtime_state(backend_name: str) -> dict[str, Any]:
    state = RECOVERY_STATE.setdefault(
        backend_name,
        {
            "failure_count": 0,
            "circuit_open_until": None,
            "last_error": None,
            "last_error_class": None,
            "last_probe_at": None,
            "last_success_at": None,
            "recovery_in_progress": False,
            "recovery_attempts": 0,
            "recovery_state": "idle",
            "last_repair_action": None,
        },
    )
    return state


def _circuit_is_open(state: dict[str, Any]) -> bool:
    until = state.get("circuit_open_until")
    if not until:
        return False
    try:
        return _utcnow() < dt.datetime.fromisoformat(until)
    except ValueError:
        return False


def _record_backend_success(backend_name: str) -> None:
    state = _backend_runtime_state(backend_name)
    state["failure_count"] = 0
    state["circuit_open_until"] = None
    state["last_error"] = None
    state["last_error_class"] = None
    state["last_probe_at"] = _isoformat()
    state["last_success_at"] = state["last_probe_at"]
    state["recovery_in_progress"] = False
    state["recovery_attempts"] = 0
    state["recovery_state"] = "idle"


def _record_backend_failure(backend_name: str, error_message: str, error_class: str | None) -> None:
    state = _backend_runtime_state(backend_name)
    policy = _policy_for(backend_name)
    state["failure_count"] = int(state.get("failure_count", 0)) + 1
    state["last_error"] = error_message
    state["last_error_class"] = error_class
    state["last_probe_at"] = _isoformat()
    threshold = max(1, int(policy.get("circuit_failure_threshold", 1)))
    if state["failure_count"] >= threshold:
        cooldown = float(policy.get("circuit_cooldown_seconds", 0.0))
        state["circuit_open_until"] = _isoformat(_utcnow() + dt.timedelta(seconds=cooldown))


def _build_repair_action(backend_name: str, active_action: dict[str, Any] | None = None) -> dict[str, Any]:
    state = _backend_runtime_state(backend_name)
    policy = _policy_for(backend_name)
    manual = _manual_remediation_summary(backend_name)
    recommended = bool(
        manual
        and (
            state.get("recovery_state") == "exhausted"
            or not policy.get("auto_recovery_allowed")
            or state.get("last_error")
        )
    )
    return {
        "auto_recovery_allowed": bool(policy.get("auto_recovery_allowed")),
        "auto_recovery_in_progress": bool(state.get("recovery_in_progress")),
        "auto_recovery_attempts": int(state.get("recovery_attempts", 0)),
        "auto_recovery_max_attempts": int(policy.get("max_recovery_attempts", 0)),
        "last_action": active_action or state.get("last_repair_action"),
        "manual_remediation_available": bool(manual),
        "manual_remediation_recommended": recommended,
        "manual_remediation": manual,
    }


def _build_status_envelope(
    backend_name: str,
    *,
    available: bool,
    primary_ok: bool,
    fallback_used: bool,
    error_class: str | None,
    evidence: list[dict[str, Any]],
    raw_status: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
    repair_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = _backend_runtime_state(backend_name)
    if available and primary_ok and not fallback_used and not error_class and not state.get("recovery_in_progress"):
        status_value = "healthy"
    elif available or fallback_used or state.get("recovery_in_progress"):
        status_value = "degraded"
    elif raw_status is None and error_class is None:
        status_value = "unknown"
    else:
        status_value = "broken"
    envelope = {
        "backend": backend_name,
        "status": status_value,
        "primary_ok": primary_ok,
        "fallback_used": fallback_used,
        "error_class": error_class,
        "last_probe_at": state.get("last_probe_at") or _isoformat(),
        "repair_action": repair_action or _build_repair_action(backend_name),
        "evidence": evidence,
        "recovery_in_progress": bool(state.get("recovery_in_progress")),
        "recovery_attempts": int(state.get("recovery_attempts", 0)),
        "recovery_state": state.get("recovery_state", "idle"),
        "circuit_open_until": state.get("circuit_open_until"),
        "last_error": state.get("last_error"),
        "last_error_class": state.get("last_error_class"),
    }
    if raw_status is not None:
        envelope["raw_status"] = raw_status
    if request_context is not None:
        envelope["request_context"] = request_context
    return envelope


def _execute_auto_recovery_action(backend_name: str, action_name: str) -> dict[str, Any]:
    raise RuntimeError(f"Unsupported auto recovery action for {backend_name}: {action_name}")


async def _post_recovery_probe(backend_name: str) -> None:
    state = _backend_runtime_state(backend_name)
    policy = _policy_for(backend_name)
    try:
        await asyncio.sleep(float(policy.get("recovery_backoff_seconds", 0.0)))
        backend = registry.get(backend_name)
        if backend is None:
            raise RuntimeError(f"Backend not registered: {backend_name}")
        status = await backend.status()
        state["last_probe_at"] = _isoformat()
        if status.get("available"):
            _record_backend_success(backend_name)
            if isinstance(state.get("last_repair_action"), dict):
                state["last_repair_action"]["verification"] = "passed"
                state["last_repair_action"]["verified_at"] = _isoformat()
            return

        error_message = (
            status.get("error")
            or "; ".join(status.get("hints", []))
            or "post-recovery probe still reports backend unavailable"
        )
        error_class = _classify_error_message(error_message, status)
        _record_backend_failure(backend_name, error_message, error_class)
        state["recovery_in_progress"] = False
        state["recovery_state"] = (
            "exhausted"
            if int(state.get("recovery_attempts", 0)) >= int(policy.get("max_recovery_attempts", 0))
            else "idle"
        )
        if isinstance(state.get("last_repair_action"), dict):
            state["last_repair_action"]["verification"] = "failed"
            state["last_repair_action"]["verified_at"] = _isoformat()
            state["last_repair_action"]["verification_error"] = error_message
    except Exception as exc:
        _record_backend_failure(backend_name, str(exc), _classify_error_message(str(exc)))
        state["recovery_in_progress"] = False
        state["recovery_state"] = (
            "exhausted"
            if int(state.get("recovery_attempts", 0)) >= int(policy.get("max_recovery_attempts", 0))
            else "idle"
        )
        if isinstance(state.get("last_repair_action"), dict):
            state["last_repair_action"]["verification"] = "failed"
            state["last_repair_action"]["verified_at"] = _isoformat()
            state["last_repair_action"]["verification_error"] = str(exc)
    finally:
        RECOVERY_TASKS.pop(backend_name, None)


async def _trigger_auto_recovery(
    backend_name: str,
    *,
    error_class: str | None,
    reason: str,
) -> dict[str, Any] | None:
    policy = _policy_for(backend_name)
    state = _backend_runtime_state(backend_name)
    eligible_classes = set(policy.get("eligible_error_classes", []))
    if not policy.get("auto_recovery_allowed"):
        return None
    if eligible_classes and error_class not in eligible_classes:
        return None
    if state.get("recovery_in_progress"):
        return state.get("last_repair_action")
    if int(state.get("recovery_attempts", 0)) >= int(policy.get("max_recovery_attempts", 0)):
        state["recovery_state"] = "exhausted"
        return state.get("last_repair_action")

    actions = list(policy.get("auto_recovery_actions", []))
    if not actions:
        return None

    action_name = actions[0]
    state["recovery_in_progress"] = True
    state["recovery_attempts"] = int(state.get("recovery_attempts", 0)) + 1
    state["recovery_state"] = "running"
    try:
        action_result = _execute_auto_recovery_action(backend_name, action_name)
        action_result["reason"] = reason
        action_result["attempt"] = state["recovery_attempts"]
        state["last_repair_action"] = action_result
        RECOVERY_TASKS[backend_name] = asyncio.create_task(_post_recovery_probe(backend_name))
        return action_result
    except Exception as exc:
        action_result = {
            "kind": "auto_recovery",
            "action": action_name,
            "reason": reason,
            "attempt": state["recovery_attempts"],
            "triggered_at": _isoformat(),
            "status": "failed_to_start",
            "error": str(exc),
        }
        state["last_repair_action"] = action_result
        state["recovery_in_progress"] = False
        state["recovery_state"] = (
            "exhausted"
            if int(state.get("recovery_attempts", 0)) >= int(policy.get("max_recovery_attempts", 0))
            else "idle"
        )
        return action_result


def _fallback_backend_names(primary_backend_name: str) -> list[str]:
    names: list[str] = []
    policy_names = _policy_for(primary_backend_name).get("fallback_backends", [])
    registry_names = _registry_entry(primary_backend_name).get("fallback_chain", [])
    for candidate in [*policy_names, *registry_names]:
        if candidate and candidate != primary_backend_name and candidate not in names:
            names.append(candidate)
    for backend in registry.all():
        if backend.name != primary_backend_name and backend.name not in names:
            names.append(backend.name)
    return names


async def _retry_backend_status(
    backend: "RAGBackend",
    *,
    force_probe: bool = False,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = _policy_for(backend.name)
    state = _backend_runtime_state(backend.name)

    if _circuit_is_open(state) and not force_probe:
        error_message = f"Circuit breaker open until {state['circuit_open_until']}"
        return _build_status_envelope(
            backend.name,
            available=False,
            primary_ok=False,
            fallback_used=False,
            error_class="gateway_error",
            evidence=[{"kind": "circuit_breaker", "detail": error_message}],
            raw_status={"available": False, "error": error_message},
            request_context=request_context,
        )

    attempts = max(1, int(policy.get("retry_attempts", BACKEND_RETRY_ATTEMPTS)))
    delay = float(policy.get("retry_delay_seconds", BACKEND_RETRY_DELAY_SECONDS))
    last_status: dict[str, Any] = {"available": False}
    last_error = "backend unavailable"

    for attempt in range(1, attempts + 1):
        try:
            last_status = await backend.status()
            if last_status.get("available"):
                _record_backend_success(backend.name)
                last_status["attempts"] = attempt
                return _build_status_envelope(
                    backend.name,
                    available=True,
                    primary_ok=True,
                    fallback_used=False,
                    error_class=None,
                    evidence=[{"kind": "probe", "attempt": attempt, "detail": "primary health probe passed"}],
                    raw_status=last_status,
                    request_context=request_context,
                )
            last_error = (
                last_status.get("error")
                or "; ".join(last_status.get("hints", []))
                or "backend reported unavailable"
            )
        except Exception as exc:
            last_error = str(exc)
            last_status = {"available": False, "error": last_error}
        if attempt < attempts:
            await asyncio.sleep(delay)

    error_class = _classify_error_message(last_error, last_status)
    _record_backend_failure(backend.name, last_error, error_class)
    repair_action = None if force_probe else await _trigger_auto_recovery(
        backend.name,
        error_class=error_class,
        reason=last_error,
    )
    last_status["attempts"] = attempts
    return _build_status_envelope(
        backend.name,
        available=False,
        primary_ok=False,
        fallback_used=False,
        error_class=error_class,
        evidence=[
            {
                "kind": "probe_failure",
                "attempts": attempts,
                "detail": last_error,
            }
        ],
        raw_status=last_status,
        request_context=request_context,
        repair_action=_build_repair_action(backend.name, repair_action),
    )


async def _query_with_retry(
    backend: "RAGBackend",
    query: str,
    mode: str,
    limit: int,
    *,
    request_context: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = _policy_for(backend.name)
    state = _backend_runtime_state(backend.name)

    if _circuit_is_open(state):
        error_message = f"Circuit breaker open until {state['circuit_open_until']}"
        return [], {
            "usable": False,
            "attempts": 0,
            "error_text": error_message,
            "error_class": "gateway_error",
            "repair_action": _build_repair_action(backend.name),
        }

    attempts = max(1, int(policy.get("retry_attempts", BACKEND_RETRY_ATTEMPTS)))
    delay = float(policy.get("retry_delay_seconds", BACKEND_RETRY_DELAY_SECONDS))
    last_results: list[dict[str, Any]] = []
    last_error = "backend query failed"

    for attempt in range(1, attempts + 1):
        last_results = await backend.query(query, mode=mode, limit=limit)
        if _results_usable(last_results):
            _record_backend_success(backend.name)
            return _annotate_results(
                last_results,
                backend_name=backend.name,
                attempt=attempt,
                request_context=request_context,
            ), {
                "usable": True,
                "attempts": attempt,
                "error_text": None,
                "error_class": None,
                "repair_action": _build_repair_action(backend.name),
            }
        last_error = _results_error_text(last_results) or "backend returned no usable results"
        if attempt < attempts:
            await asyncio.sleep(delay)

    error_class = _classify_error_message(last_error, last_results)
    _record_backend_failure(backend.name, last_error, error_class)
    repair_action = await _trigger_auto_recovery(
        backend.name,
        error_class=error_class,
        reason=last_error,
    )
    return _annotate_results(
        last_results,
        backend_name=backend.name,
        attempt=attempts,
        request_context=request_context,
    ), {
        "usable": False,
        "attempts": attempts,
        "error_text": last_error,
        "error_class": error_class,
        "repair_action": _build_repair_action(backend.name, repair_action),
    }


class RAGBackend(ABC):
    name: str
    description: str

    @abstractmethod
    async def is_available(self) -> bool:
        """Return True if this backend can service queries right now."""
        ...

    @abstractmethod
    async def query(self, query: str, mode: str = "hybrid", limit: int = 5) -> list[dict]:
        """Return list of {score, text, source, metadata} dicts."""
        ...

    @abstractmethod
    async def status(self) -> dict:
        """Return health/stats dict."""
        ...


class BackendRegistry:
    def __init__(self):
        self._backends: dict[str, RAGBackend] = {}

    def register(self, backend: RAGBackend):
        self._backends[backend.name] = backend

    def all(self) -> list[RAGBackend]:
        return list(self._backends.values())

    def get(self, name: str) -> RAGBackend | None:
        return self._backends.get(name)


registry = BackendRegistry()


class MemoryweaverBackend(RAGBackend):
    name = "memoryweaver"
    description = (
        "Hybrid BM25+semantic search over accumulated session decisions, inferred patterns, "
        "and project knowledge"
    )

    def _imports_ok(self) -> tuple[bool, str | None]:
        try:
            import chromadb  # noqa: F401
            from memoryweaver.store.chroma import ChromaDBAdapter  # noqa: F401
            from memoryweaver.store.fts import FTS5Adapter  # noqa: F401

            return True, None
        except Exception as exc:
            return False, str(exc)

    async def is_available(self) -> bool:
        ok, _ = self._imports_ok()
        return ok

    async def query(self, query: str, mode: str = "hybrid", limit: int = 5) -> list[dict]:
        try:
            from memoryweaver.store.chroma import ChromaDBAdapter

            store = ChromaDBAdapter()
            if mode == "semantic":
                results = store.query_semantic(
                    text=query,
                    top_k=limit,
                    include_inferred=True,
                )
            else:
                results = store.query_hybrid(
                    text=query,
                    top_k=limit,
                    include_inferred=True,
                )

            out = []
            for item in results:
                out.append(
                    {
                        "score": item.get("rrf_score", item.get("combined_score", item.get("score", 0))),
                        "text": item.get("document", item.get("text", "")),
                        "source": (item.get("metadata") or {}).get("source_uri", "memoryweaver"),
                        "metadata": item.get("metadata", {}),
                    }
                )
            return out
        except Exception as exc:
            return [{"score": 0, "text": f"Error: {exc}", "source": "memoryweaver", "metadata": {}}]

    async def status(self) -> dict:
        ok, err = self._imports_ok()
        if not ok:
            return {"available": False, "error": err}

        try:
            from memoryweaver.store.chroma import ChromaDBAdapter
            from memoryweaver.store.fts import FTS5Adapter

            spool_dir = Path(os.environ.get("MEMORYWEAVER_DATA_DIR", DEFAULT_MEMORYWEAVER_DATA_DIR)) / "spool"
            store = ChromaDBAdapter()
            explicit_count = store.count("explicit_memories")
            inferred_count = store.count("inferred_memories")
            pending = len(list(spool_dir.glob("*.json"))) if spool_dir.exists() else 0
            done = len(list(spool_dir.glob("*.done"))) if spool_dir.exists() else 0
            errors = len(list(spool_dir.glob("*.error"))) if spool_dir.exists() else 0

            warnings = []
            chroma_total = explicit_count + inferred_count
            fts_count = 0
            try:
                fts = FTS5Adapter()
                fts_count = fts.count()
                if fts_count == 0 and chroma_total > 0:
                    warnings.append("FTS index empty -- run: python -m memoryweaver sync-fts")
                elif fts_count < chroma_total:
                    warnings.append(
                        f"FTS ({fts_count}) < Chroma ({chroma_total}) -- run: python -m memoryweaver sync-fts"
                    )
            except Exception as exc:
                warnings.append(f"FTS unavailable; Memoryweaver semantic search remains available: {exc}")

            return {
                "available": True,
                "data_dir": os.environ.get("MEMORYWEAVER_DATA_DIR", DEFAULT_MEMORYWEAVER_DATA_DIR),
                "explicit_memories": explicit_count,
                "inferred_memories": inferred_count,
                "fts_index": fts_count,
                "spool_pending": pending,
                "spool_done": done,
                "spool_errors": errors,
                "warnings": warnings,
            }
        except Exception as exc:
            return {"available": False, "error": str(exc)}


class GraphRAGBackend(RAGBackend):
    name = "graphrag"
    description = (
        "Community/relationship-aware graph queries over the MyVault vault "
        "(requires a complete GraphRAG index)"
    )

    def __init__(self, root: str = GRAPHRAG_ROOT):
        self.root = root
        self.output_dir = os.path.join(self.root, "output")

    def _missing_outputs(self) -> list[str]:
        return [
            name for name in GRAPHRAG_REQUIRED_OUTPUTS if not os.path.exists(os.path.join(self.output_dir, name))
        ]

    def _runtime_available(self) -> tuple[bool, str | None]:
        if not os.path.exists(GRAPHRAG_WINDOWS_PYTHON):
            return False, f"Missing Windows Python runtime: {GRAPHRAG_WINDOWS_PYTHON}"
        return True, None

    def _stats_summary(self) -> dict[str, Any] | None:
        stats_path = os.path.join(self.output_dir, "stats.json")
        if not os.path.exists(stats_path):
            return None
        try:
            with open(stats_path, "r", encoding="utf-8") as handle:
                stats = json.load(handle)
            return {
                "stats_path": stats_path,
                "num_documents": stats.get("num_documents"),
                "total_runtime": stats.get("total_runtime"),
                "workflows_completed": sorted((stats.get("workflows") or {}).keys()),
            }
        except Exception as exc:
            return {"stats_path": stats_path, "error": str(exc)}

    def _build_wrapper_script(self, method: str, query: str) -> str:
        payload = json.dumps(
            {
                "site_packages": GRAPHRAG_WINDOWS_SITE_PACKAGES,
                "root": GRAPHRAG_WINDOWS_ROOT,
                "method": method,
                "query": query,
            }
        )
        return (
            "import json, runpy, sys; "
            f"cfg=json.loads({json.dumps(payload)}); "
            "sys.path.insert(0, cfg['site_packages']); "
            "sys.path.insert(0, cfg['site_packages'] + r'\\win32'); "
            "sys.path.insert(0, cfg['site_packages'] + r'\\win32\\lib'); "
            "sys.argv=['graphrag','query','--root',cfg['root'],'--method',cfg['method'],cfg['query']]; "
            "runpy.run_module('graphrag', run_name='__main__')"
        )

    async def is_available(self) -> bool:
        missing = self._missing_outputs()
        runtime_ok, _ = self._runtime_available()
        return not missing and runtime_ok

    async def query(self, query: str, mode: str = "local", limit: int = 5) -> list[dict]:
        method = "local" if mode in ("local", "hybrid") else "global"
        missing = self._missing_outputs()
        if missing:
            return [
                {
                    "score": 0,
                    "text": "Error: GraphRAG index is incomplete. Missing required output parquet files.",
                    "source": "graphrag",
                    "metadata": {"missing_outputs": missing, "root": self.root},
                }
            ]
        try:
            command = [
                GRAPHRAG_WINDOWS_PYTHON,
                "-c",
                self._build_wrapper_script(method, query),
            ]
            timeout = float(_policy_for(self.name).get("query_timeout_seconds", 900.0))
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            combined = stdout if result.returncode == 0 else (stdout + ("\n" if stdout and stderr else "") + stderr)
            combined = combined.strip() or f"GraphRAG exited with code {result.returncode}"
            score = 1.0 if result.returncode == 0 else 0
            return [
                {
                    "score": score,
                    "text": combined,
                    "source": "graphrag",
                    "metadata": {"method": method, "exit_code": result.returncode},
                }
            ]
        except Exception as exc:
            return [{"score": 0, "text": f"Error: {exc}", "source": "graphrag", "metadata": {}}]

    async def status(self) -> dict:
        missing = self._missing_outputs()
        runtime_ok, runtime_error = self._runtime_available()
        available = not missing and runtime_ok
        hints = []
        if missing:
            hints.append("GraphRAG output is incomplete; rerun sync-graphrag-input.py then index-graphrag.bat")
        if missing and os.path.exists(os.path.join(self.output_dir, "text_units.parquet")):
            hints.append("Partial preprocessing exists, but graph/community parquet outputs were not generated")
        return {
            "available": available,
            "index_path": self.output_dir,
            "index_exists": os.path.exists(self.output_dir),
            "missing_required_outputs": missing,
            "windows_python": GRAPHRAG_WINDOWS_PYTHON,
            "runtime_ok": runtime_ok,
            "runtime_error": runtime_error,
            "stats": self._stats_summary(),
            "hints": hints,
        }


registry.register(MemoryweaverBackend())
registry.register(GraphRAGBackend())


def _tool_input_schema(include_backend: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "query": {"type": "string"},
        "mode": {"type": "string", "default": "hybrid"},
        "limit": {"type": "integer", "default": 5},
        "fallback": {"type": "boolean", "default": True},
        "context": {"type": "object", "additionalProperties": True},
    }
    if include_backend:
        properties["backend"] = {
            "type": "string",
            "enum": ["memoryweaver", "graphrag"],
        }
    return {"type": "object", "properties": properties, "required": ["query"]}


async def _run_single_backend_query(
    backend_name: str,
    query: str,
    mode: str,
    limit: int,
    fallback: bool,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    backend = registry.get(backend_name)
    if backend is None:
        return {
            "request_context": request_context,
            "backend": backend_name,
            "status": _build_status_envelope(
                backend_name,
                available=False,
                primary_ok=False,
                fallback_used=False,
                error_class="gateway_error",
                evidence=[{"kind": "configuration_error", "detail": f"Unknown backend: {backend_name}"}],
                request_context=request_context,
            ),
            "results": [],
        }

    primary_status = await _retry_backend_status(backend, request_context=request_context)
    primary_query_meta: dict[str, Any] | None = None
    primary_results: list[dict[str, Any]] = []

    if primary_status.get("primary_ok"):
        primary_results, primary_query_meta = await _query_with_retry(
            backend,
            query,
            mode=mode,
            limit=limit,
            request_context=request_context,
        )
        if primary_query_meta["usable"]:
            status = _build_status_envelope(
                backend_name,
                available=True,
                primary_ok=True,
                fallback_used=False,
                error_class=None,
                evidence=[
                    {"kind": "primary_query", "detail": "primary backend query succeeded"},
                    {"kind": "status_probe", "detail": primary_status.get("status")},
                ],
                raw_status=primary_status.get("raw_status"),
                request_context=request_context,
            )
            return {
                "request_context": request_context,
                "backend": backend_name,
                "status": status,
                "results": primary_results,
                "fallback_used": False,
            }

    fallback_statuses: dict[str, Any] = {}
    fallback_results: list[dict[str, Any]] = []
    fallback_backend: str | None = None

    if fallback:
        for candidate_name in _fallback_backend_names(backend_name):
            candidate = registry.get(candidate_name)
            if candidate is None:
                continue
            candidate_context = dict(request_context)
            candidate_context["selected_backend"] = candidate_name
            candidate_status = await _retry_backend_status(candidate, request_context=candidate_context)
            fallback_statuses[candidate_name] = candidate_status
            if not candidate_status.get("primary_ok"):
                continue
            candidate_results, candidate_meta = await _query_with_retry(
                candidate,
                query,
                mode=mode,
                limit=limit,
                request_context=candidate_context,
            )
            if not candidate_meta["usable"]:
                fallback_statuses[candidate_name]["query_error"] = candidate_meta
                continue
            fallback_backend = candidate_name
            fallback_results = _annotate_results(
                candidate_results,
                backend_name=candidate_name,
                attempt=candidate_meta["attempts"],
                request_context=request_context,
                degraded=True,
                fallback_source=backend_name,
            )
            break

    failure_class = (
        primary_query_meta["error_class"]
        if primary_query_meta and primary_query_meta.get("error_class")
        else primary_status.get("error_class")
    )
    failure_detail = (
        primary_query_meta["error_text"]
        if primary_query_meta and primary_query_meta.get("error_text")
        else primary_status.get("last_error")
        or (primary_status.get("raw_status") or {}).get("error")
        or "primary backend failed"
    )
    status = _build_status_envelope(
        backend_name,
        available=bool(fallback_results),
        primary_ok=False,
        fallback_used=bool(fallback_results),
        error_class="degraded_fallback" if fallback_results else failure_class,
        evidence=[
            {"kind": "primary_failure", "detail": failure_detail},
            {"kind": "fallback_backend", "detail": fallback_backend} if fallback_backend else {"kind": "fallback_backend", "detail": None},
        ],
        raw_status=primary_status.get("raw_status"),
        request_context=request_context,
    )
    return {
        "request_context": request_context,
        "backend": backend_name,
        "status": status,
        "primary_status": primary_status,
        "fallback_used": bool(fallback_results),
        "fallback_backend": fallback_backend,
        "fallback_statuses": fallback_statuses,
        "results": fallback_results or primary_results,
    }


def _gateway_overall_status(backend_states: dict[str, dict[str, Any]], request_context: dict[str, Any]) -> dict[str, Any]:
    available = any(state.get("primary_ok") or state.get("fallback_used") for state in backend_states.values())
    primary_ok = bool(backend_states) and all(state.get("primary_ok") for state in backend_states.values())
    fallback_used = any(state.get("fallback_used") for state in backend_states.values())
    first_error_class = next((state.get("error_class") for state in backend_states.values() if state.get("error_class")), None)
    evidence = [
        {
            "backend": backend_name,
            "status": state.get("status"),
            "error_class": state.get("error_class"),
        }
        for backend_name, state in backend_states.items()
    ]
    return _build_status_envelope(
        "rag-orchestrator",
        available=available,
        primary_ok=primary_ok,
        fallback_used=fallback_used,
        error_class=None if primary_ok else first_error_class,
        evidence=evidence,
        request_context=request_context,
    )


async def _run_fanout_query(
    query: str,
    mode: str,
    limit: int,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    all_results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    backend_states: dict[str, dict[str, Any]] = {}

    for backend in registry.all():
        backend_context = dict(request_context)
        backend_context["selected_backend"] = backend.name
        status = await _retry_backend_status(backend, request_context=backend_context)
        backend_states[backend.name] = status
        if not status.get("primary_ok"):
            skipped.append({"backend": backend.name, "status": status})
            continue
        results, query_meta = await _query_with_retry(
            backend,
            query,
            mode=mode,
            limit=limit,
            request_context=backend_context,
        )
        if not query_meta["usable"]:
            backend_states[backend.name] = _build_status_envelope(
                backend.name,
                available=False,
                primary_ok=False,
                fallback_used=False,
                error_class=query_meta["error_class"],
                evidence=[{"kind": "query_failure", "detail": query_meta["error_text"]}],
                raw_status=status.get("raw_status"),
                request_context=backend_context,
                repair_action=query_meta["repair_action"],
            )
            skipped.append(
                {
                    "backend": backend.name,
                    "status": backend_states[backend.name],
                    "query_error": query_meta,
                }
            )
            continue
        for result in results:
            result["backend"] = backend.name
            all_results.append(result)

    all_results.sort(key=lambda item: item.get("score", 0), reverse=True)
    return {
        "request_context": request_context,
        "status": _gateway_overall_status(backend_states, request_context),
        "query": query,
        "results": all_results,
        "backends_queried": [backend for backend, state in backend_states.items() if state.get("primary_ok")],
        "backend_states": backend_states,
        "skipped": skipped,
        "total": len(all_results),
    }


async def _collect_status_snapshot(request_context: dict[str, Any]) -> dict[str, Any]:
    backend_states: dict[str, dict[str, Any]] = {}
    for backend in registry.all():
        backend_context = dict(request_context)
        backend_context["selected_backend"] = backend.name
        backend_states[backend.name] = await _retry_backend_status(backend, request_context=backend_context)
    return {
        "request_context": request_context,
        "status": _gateway_overall_status(backend_states, request_context),
        "backends": backend_states,
    }


async def _run_gateway_query(arguments: dict[str, Any]) -> dict[str, Any]:
    request_context = _build_request_context(arguments, intent_override="query")
    context_missing = _validate_request_context(request_context)
    if context_missing:
        return {
            "request_context": request_context,
            "status": _build_status_envelope(
                "rag-orchestrator",
                available=False,
                primary_ok=False,
                fallback_used=False,
                error_class="protocol_error",
                evidence=[{"kind": "request_context_error", "detail": ", ".join(context_missing)}],
                request_context=request_context,
            ),
            "results": [],
        }

    query = arguments.get("query", "")
    mode = arguments.get("mode", "hybrid")
    limit = arguments.get("limit", 5)
    fallback = arguments.get("fallback", True)
    preferred_backend = arguments.get("preferred_backend")

    if preferred_backend and preferred_backend not in ("auto", "all"):
        request_context["selected_backend"] = preferred_backend
        return await _run_single_backend_query(
            preferred_backend,
            query,
            mode,
            limit,
            fallback,
            request_context,
        )
    request_context["selected_backend"] = "all"
    return await _run_fanout_query(query, mode, limit, request_context)


app = Server("rag-orchestrator") if Server is not None else None

if app is not None:

    @app.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="gateway_query",
                description=(
                    "Unified orchestration endpoint for MyVault RAG. "
                    "Accepts per-call context, applies retry/circuit policy, "
                    "returns degraded truth when fallback is used, and exposes recovery state."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The query to run across RAG backends"},
                        "preferred_backend": {
                            "type": "string",
                            "enum": ["auto", "memoryweaver", "graphrag"],
                            "default": "auto",
                        },
                        "mode": {"type": "string", "enum": ["hybrid", "local", "global"], "default": "hybrid"},
                        "limit": {"type": "integer", "default": 5},
                        "fallback": {"type": "boolean", "default": True},
                        "context": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="gateway_status",
                description="Unified truth-state status for the MyVault RAG gateway and all registered backends.",
                inputSchema={"type": "object", "properties": {"context": {"type": "object", "additionalProperties": True}}},
            ),
            types.Tool(
                name="rag_query",
                description=(
                    "Compatibility alias for querying all available RAG backends simultaneously. "
                    "Returns unified status, backend states, and merged results."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The query to run across all backends"},
                        "mode": {"type": "string", "enum": ["hybrid", "local", "global"], "default": "hybrid"},
                        "limit": {"type": "integer", "default": 5, "description": "Results per backend"},
                        "context": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="rag_backend_query",
                description=(
                    "Compatibility alias for querying a single specific RAG backend by name. "
                    "Returns degraded truth and fallback state instead of silently flattening failures."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "backend": {"type": "string", "enum": ["memoryweaver", "graphrag"]},
                        "query": {"type": "string"},
                        "mode": {"type": "string", "default": "hybrid"},
                        "limit": {"type": "integer", "default": 5},
                        "fallback": {"type": "boolean", "default": True},
                        "context": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["backend", "query"],
                },
            ),
            types.Tool(
                name="rag_status",
                description="Compatibility alias for unified backend status with truth-state and recovery metadata.",
                inputSchema={"type": "object", "properties": {"context": {"type": "object", "additionalProperties": True}}},
            ),
            types.Tool(
                name="rag_backends_list",
                description="List all registered backends and their descriptions.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]


    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name == "rag_backends_list":
            backends = []
            for backend in registry.all():
                backends.append(
                    {
                        "name": backend.name,
                        "description": backend.description,
                        "policy": _policy_for(backend.name),
                        "registry": _registry_entry(backend.name),
                        "manual_remediation": _manual_remediation_summary(backend.name),
                    }
                )
            return [types.TextContent(type="text", text=json.dumps(backends, indent=2))]

        if name in {"gateway_status", "rag_status"}:
            request_context = _build_request_context(arguments, intent_override="status", selected_backend="all")
            payload = await _collect_status_snapshot(request_context)
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "gateway_query":
            payload = await _run_gateway_query(arguments)
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "rag_backend_query":
            request_context = _build_request_context(arguments, intent_override="query")
            backend_name = arguments.get("backend")
            payload = await _run_single_backend_query(
                backend_name,
                arguments.get("query", ""),
                arguments.get("mode", "hybrid"),
                arguments.get("limit", 5),
                arguments.get("fallback", True),
                request_context,
            )
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "rag_query":
            request_context = _build_request_context(arguments, intent_override="query", selected_backend="all")
            payload = await _run_fanout_query(
                arguments.get("query", ""),
                arguments.get("mode", "hybrid"),
                arguments.get("limit", 5),
                request_context,
            )
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--status-json", action="store_true")
    args, _ = parser.parse_known_args(sys.argv[1:])

    if args.status_json:
        request_context = _build_request_context({"context": {}}, intent_override="status", selected_backend="all")
        payload = await _collect_status_snapshot(request_context)
        print(json.dumps(payload, indent=2))
        return

    if app is None or stdio_server is None:
        raise RuntimeError("MCP runtime imports are unavailable; use --status-json for probe mode")

    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
