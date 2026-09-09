"""Append-only, content-free token and compute usage accounting."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class UsageLedgerError(RuntimeError):
    """A usage event or ledger operation violated the closed contract."""


UNAVAILABLE = "UNAVAILABLE"
SCHEMA_VERSION = "1.0"
HARNESSES = frozenset({
    "hermes", "codex", "claude-code", "moa", "opencode", "pi", "antigravity",
})
TASK_TIERS = frozenset({"atomic", "standard", "complex", "high_risk", UNAVAILABLE})
ADMISSION = frozenset({"ADMITTED", "DENIED", UNAVAILABLE})
OUTCOMES = frozenset({"complete", "blocked", "fallback", "failed"})
BASE_EVENT_KEYS = frozenset({
    "schema_version", "event_id", "execution_id", "created_at", "harness",
    "task_class", "task_tier", "model_route_fingerprint", "input_tokens",
    "output_tokens", "cache_read_tokens", "cache_write_tokens", "wall_time_ms",
    "cpu_admission", "memory_admission", "retry_count", "outcome", "verified",
})
CONTROL_KEYS = frozenset({
    "depth_profile", "depth_evidence_fingerprint", "cache_status",
    "cache_artifact_type", "cache_key_fingerprint", "cache_bytes_reused",
    "cache_reason",
    "packet_bytes", "retrieved_bytes", "summary_bytes", "dropped_section_categories",
    "context_pressure_band", "rendering_profile",
    "stop_decision", "stop_reason", "stop_rule",
    "stage_timings_ms",
})
EVENT_KEYS = BASE_EVENT_KEYS | CONTROL_KEYS
FORBIDDEN_CONTENT_KEYS = frozenset({
    "prompt", "source", "source_text", "command_output", "credentials",
    "provider_token", "api_key", "password", "secret", "raw_exception",
})
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
STAGE_TIMING_KEYS = frozenset({
    "adapter", "routing", "provider_wait", "tool_execution", "handoff",
    "evidence_collection", "persistence",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _count(value: Any, name: str) -> int | str:
    if value == UNAVAILABLE:
        return UNAVAILABLE
    if type(value) is not int or value < 0:
        raise UsageLedgerError(f"{name} must be a non-negative integer or UNAVAILABLE")
    return value


def validate_usage_event(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not BASE_EVENT_KEYS.issubset(value) or set(value) - EVENT_KEYS:
        extra = set(value) - EVENT_KEYS if isinstance(value, Mapping) else set()
        if extra & FORBIDDEN_CONTENT_KEYS:
            raise UsageLedgerError("raw content fields are forbidden")
        raise UsageLedgerError("usage event keys are not closed")
    event = dict(value)
    event.setdefault("depth_profile", UNAVAILABLE)
    event.setdefault("depth_evidence_fingerprint", None)
    event.setdefault("cache_status", UNAVAILABLE)
    event.setdefault("cache_artifact_type", UNAVAILABLE)
    event.setdefault("cache_key_fingerprint", None)
    event.setdefault("cache_bytes_reused", 0)
    event.setdefault("cache_reason", UNAVAILABLE)
    event.setdefault("packet_bytes", 0)
    event.setdefault("retrieved_bytes", 0)
    event.setdefault("summary_bytes", 0)
    event.setdefault("dropped_section_categories", [])
    event.setdefault("context_pressure_band", UNAVAILABLE)
    event.setdefault("rendering_profile", UNAVAILABLE)
    event.setdefault("stop_decision", UNAVAILABLE)
    event.setdefault("stop_reason", UNAVAILABLE)
    event.setdefault("stop_rule", UNAVAILABLE)
    event.setdefault("stage_timings_ms", {})
    if event["schema_version"] != SCHEMA_VERSION:
        raise UsageLedgerError("unsupported usage event schema")
    if not isinstance(event["event_id"], str) or not _HEX_32.fullmatch(event["event_id"]):
        raise UsageLedgerError("event_id must be 32 lowercase hex characters")
    if not isinstance(event["execution_id"], str) or not 1 <= len(event["execution_id"]) <= 128:
        raise UsageLedgerError("execution_id is invalid")
    try:
        datetime.fromisoformat(str(event["created_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsageLedgerError("created_at must be an ISO timestamp") from exc
    if event["harness"] not in HARNESSES:
        raise UsageLedgerError("harness is not allowed")
    if not isinstance(event["task_class"], str) or not 1 <= len(event["task_class"]) <= 64:
        raise UsageLedgerError("task_class is invalid")
    if event["task_tier"] not in TASK_TIERS:
        raise UsageLedgerError("task_tier is invalid")
    if not isinstance(event["model_route_fingerprint"], str) or not _HEX_64.fullmatch(event["model_route_fingerprint"]):
        raise UsageLedgerError("model_route_fingerprint must be a sha256 hex digest")
    for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "wall_time_ms"):
        event[name] = _count(event[name], name)
    if event["cpu_admission"] not in ADMISSION or event["memory_admission"] not in ADMISSION:
        raise UsageLedgerError("admission outcome is invalid")
    if type(event["retry_count"]) is not int or event["retry_count"] < 0:
        raise UsageLedgerError("retry_count must be a non-negative integer")
    if event["outcome"] not in OUTCOMES:
        raise UsageLedgerError("outcome is invalid")
    if event["verified"] != UNAVAILABLE and type(event["verified"]) is not bool:
        raise UsageLedgerError("verified must be a boolean or UNAVAILABLE")
    if event["depth_profile"] not in {"compact", "balanced", "full", UNAVAILABLE}:
        raise UsageLedgerError("depth_profile is invalid")
    depth_fingerprint = event["depth_evidence_fingerprint"]
    if depth_fingerprint is not None and (not isinstance(depth_fingerprint, str) or not _HEX_64.fullmatch(depth_fingerprint)):
        raise UsageLedgerError("depth_evidence_fingerprint must be null or sha256")
    if event["cache_status"] not in {"HIT", "MISS", "DISABLED", UNAVAILABLE}:
        raise UsageLedgerError("cache_status is invalid")
    if event["cache_artifact_type"] not in {"policy_projection", UNAVAILABLE}:
        raise UsageLedgerError("cache_artifact_type is invalid")
    cache_fingerprint = event["cache_key_fingerprint"]
    if cache_fingerprint is not None and (not isinstance(cache_fingerprint, str) or not _HEX_64.fullmatch(cache_fingerprint)):
        raise UsageLedgerError("cache_key_fingerprint must be null or sha256")
    if type(event["cache_bytes_reused"]) is not int or event["cache_bytes_reused"] < 0:
        raise UsageLedgerError("cache_bytes_reused must be a non-negative integer")
    if event["cache_reason"] not in {"NOT_FOUND", "VALID", "CORRUPT", "POLICY_DISABLED", "CACHE_UNAVAILABLE", UNAVAILABLE}:
        raise UsageLedgerError("cache_reason is invalid")
    for name in ("packet_bytes", "retrieved_bytes", "summary_bytes"):
        if type(event[name]) is not int or event[name] < 0:
            raise UsageLedgerError(f"{name} must be a non-negative integer")
    categories = event["dropped_section_categories"]
    if not isinstance(categories, list) or categories != sorted(set(categories)) or any(item not in {"context", "repository", "tool_output", "policy", "history"} for item in categories):
        raise UsageLedgerError("dropped_section_categories is invalid")
    if event["context_pressure_band"] not in {"PEAK", "GOOD", "DEGRADING", "POOR", UNAVAILABLE}:
        raise UsageLedgerError("context_pressure_band is invalid")
    if event["rendering_profile"] not in {"governed", "concise_user", "narrative_user", "verbatim", UNAVAILABLE}:
        raise UsageLedgerError("rendering_profile is invalid")
    if event["stop_decision"] not in {"CONTINUE", "SHADOW_STOP", "ENFORCED_STOP", UNAVAILABLE}:
        raise UsageLedgerError("stop_decision is invalid")
    if event["stop_reason"] not in {"RULE_DISABLED", "HIGH_RISK_EXCLUDED", "MARGINAL_VALUE_AVAILABLE", "BUDGET_UNAVAILABLE", "INSUFFICIENT_EXPECTED_VALUE", "RULE_DISABLED_ERROR", "POLICY_UNAVAILABLE", UNAVAILABLE}:
        raise UsageLedgerError("stop_reason is invalid")
    if event["stop_rule"] not in {"retry", "repair", "context_expansion", UNAVAILABLE}:
        raise UsageLedgerError("stop_rule is invalid")
    stages = event["stage_timings_ms"]
    if (
        not isinstance(stages, dict)
        or set(stages) - STAGE_TIMING_KEYS
        or any(type(value) is not int or value < 0 for value in stages.values())
    ):
        raise UsageLedgerError("stage_timings_ms is invalid")
    event["stage_timings_ms"] = dict(sorted(stages.items()))
    return event


def make_usage_event(*, execution_id: str, harness: str, task_class: str,
                     model_route_fingerprint: str, outcome: str,
                     usage: Mapping[str, Any] | None = None, task_tier: str = UNAVAILABLE,
                     wall_time_ms: int | str = UNAVAILABLE, retry_count: int = 0,
                     cpu_admission: str = UNAVAILABLE, memory_admission: str = UNAVAILABLE,
                     verified: bool | str = UNAVAILABLE, depth_profile: str = UNAVAILABLE,
                     depth_evidence_fingerprint: str | None = None,
                     cache_status: str = UNAVAILABLE, cache_artifact_type: str = UNAVAILABLE,
                     cache_key_fingerprint: str | None = None, cache_bytes_reused: int = 0,
                     cache_reason: str = UNAVAILABLE, packet_bytes: int = 0,
                     retrieved_bytes: int = 0, summary_bytes: int = 0,
                     dropped_section_categories: list[str] | None = None,
                     context_pressure_band: str = UNAVAILABLE, rendering_profile: str = UNAVAILABLE,
                     stop_decision: str = UNAVAILABLE, stop_reason: str = UNAVAILABLE,
                     stop_rule: str = UNAVAILABLE,
                     stage_timings_ms: Mapping[str, int] | None = None) -> dict[str, Any]:
    reported = usage if isinstance(usage, Mapping) else {}

    def reported_count(*names: str) -> int | str:
        for name in names:
            candidate = reported.get(name)
            if type(candidate) is int and candidate >= 0:
                return candidate
        return UNAVAILABLE

    return validate_usage_event({
        "schema_version": SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "execution_id": execution_id,
        "created_at": utc_now(),
        "harness": harness,
        "task_class": task_class,
        "task_tier": task_tier,
        "model_route_fingerprint": model_route_fingerprint,
        "input_tokens": reported_count("input_tokens", "prompt_tokens"),
        "output_tokens": reported_count("output_tokens", "completion_tokens"),
        "cache_read_tokens": reported_count("cache_read_tokens", "cached_tokens"),
        "cache_write_tokens": reported_count("cache_write_tokens"),
        "wall_time_ms": wall_time_ms,
        "cpu_admission": cpu_admission,
        "memory_admission": memory_admission,
        "retry_count": retry_count,
        "outcome": outcome if outcome in OUTCOMES else "failed",
        "verified": verified,
        "depth_profile": depth_profile,
        "depth_evidence_fingerprint": depth_evidence_fingerprint,
        "cache_status": cache_status,
        "cache_artifact_type": cache_artifact_type,
        "cache_key_fingerprint": cache_key_fingerprint,
        "cache_bytes_reused": cache_bytes_reused,
        "cache_reason": cache_reason,
        "packet_bytes": packet_bytes,
        "retrieved_bytes": retrieved_bytes,
        "summary_bytes": summary_bytes,
        "dropped_section_categories": sorted(set(dropped_section_categories or [])),
        "context_pressure_band": context_pressure_band,
        "rendering_profile": rendering_profile,
        "stop_decision": stop_decision,
        "stop_reason": stop_reason,
        "stop_rule": stop_rule,
        "stage_timings_ms": dict(stage_timings_ms or {}),
    })


class UsageLedger:
    """SQLite WAL ledger that stores validated count-only events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS usage_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                execution_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                harness TEXT NOT NULL,
                event_json TEXT NOT NULL
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def append(self, value: Mapping[str, Any]) -> dict[str, Any]:
        event = validate_usage_event(value)
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO usage_events(event_id,execution_id,created_at,harness,event_json) VALUES(?,?,?,?,?)",
                    (event["event_id"], event["execution_id"], event["created_at"], event["harness"], payload),
                )
        except sqlite3.IntegrityError as exc:
            raise UsageLedgerError("duplicate usage event_id") from exc
        return event

    def history(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT event_json FROM usage_events ORDER BY sequence").fetchall()
        return [validate_usage_event(json.loads(row["event_json"])) for row in rows]
