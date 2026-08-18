"""
ProducerHook — writes versioned MemoryEnvelope JSON to spool directory.

Fixes E-003: NOT a direct function call from stop-wrap-up.py.
Instead writes a spool file; the ledger's ingest consumer polls, validates,
processes, and acks by renaming to .done.

Spool format:
  {
    "schema_version": "1.0",
    "session_id": "...",
    "timestamp": "...",
    "session_path": "/abs/path/to/session.md",
    "decision_entries": ["line1", "line2", ...],
    "project": "ExampleProject",
    "thread": null
  }

CLI usage (called by stop-wrap-up.py):
  python -m memoryweaver spool --session /path/to/session.md [--decisions /path/to/decisions.md]

Consumer usage:
  from memoryweaver.producer import SpoolConsumer
  consumer = SpoolConsumer()
  consumer.process_pending()
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from ._paths import MW_DATA_DIR
from .capture_quality import clean_memory_line, is_standalone_noise_line

_SPOOL_DIR = MW_DATA_DIR / "spool"
_SCHEMA_VERSION = "1.0"
_CAPTURE_STATUS = MW_DATA_DIR / "capture-status.json"
_LOG_DIR = MW_DATA_DIR / "logs"
_SPOOL_ERROR_LOG = _LOG_DIR / "spool-errors.jsonl"


def _runtime_data_dir() -> Path:
    """Resolve the recipient data root at call time, not module import time."""
    value = os.environ.get("MEMORYWEAVER_DATA_DIR", "").strip()
    return Path(value) if value else MW_DATA_DIR


# ---------------------------------------------------------------------------
# Producer (write side)
# ---------------------------------------------------------------------------

def write_spool(
    session_path: Optional[Path] = None,
    decisions_path: Optional[Path] = None,
    project: Optional[str] = None,
    thread: Optional[str] = None,
    harness: Optional[str] = None,
    spool_dir: Optional[Path] = None,
) -> Path:
    """
    Write a MemoryEnvelope spool file. Returns path to the .json file.
    Idempotent: if .done file for same content hash exists, returns that path.

    `harness` records which agent harness captured this (claude-code|codex|hermes);
    inferred from env if omitted. Used for the per-harness capture heartbeat so
    cross-harness capture parity is observable via `memory_status`.
    """
    spool_dir = Path(spool_dir) if spool_dir else _runtime_data_dir() / "spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    if harness is None:
        harness = _infer_harness()

    session_id = (
        session_path.stem if session_path else datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    )
    timestamp = datetime.now(tz=timezone.utc).isoformat()

    decision_entries: List[dict] = []
    if decisions_path and decisions_path.exists():
        decision_entries = _read_recent_decisions(decisions_path)

    if project is None and session_path:
        project = _infer_project(session_path)

    envelope = {
        "schema_version": _SCHEMA_VERSION,
        "session_id": session_id,
        "timestamp": timestamp,
        "session_path": str(session_path) if session_path else None,
        "decision_entries": decision_entries,
        "project": project,
        "thread": thread,
    }

    content_hash = _hash_envelope(envelope)
    for suffix in (".done", ".error"):
        existing = spool_dir / f"{content_hash}{suffix}"
        if existing.exists():
            return existing  # already processed or quarantined

    ts_prefix = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    spool_path = spool_dir / f"{ts_prefix}-{content_hash[:12]}.json"
    spool_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    _record_capture(harness, project)  # fail-open per-harness heartbeat (Phase 3)
    return spool_path


def _read_recent_decisions(path: Path) -> List[dict]:
    """Return decision lines added since last session (heuristic: last 20 lines)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = []
    for idx, line in enumerate(lines, start=1):
        cleaned = clean_memory_line(line)
        if is_standalone_noise_line(cleaned):
            continue
        entries.append({"text": cleaned, "source_file": str(path), "source_line": idx})
    return entries[-20:]  # cap at 20 most recent


def _hash_envelope(envelope: dict) -> str:
    serialised = json.dumps(
        {k: v for k, v in envelope.items() if k != "timestamp"},
        sort_keys=True,
    )
    return hashlib.sha256(serialised.encode()).hexdigest()[:32]


def _infer_project(path: Path) -> Optional[str]:
    parts = path.parts
    for i, part in enumerate(parts):
        if part.lower() == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return None


# ---------------------------------------------------------------------------
# Capture instrumentation (Phase 3) — per-harness heartbeat, fail-open
# ---------------------------------------------------------------------------

def _infer_harness() -> str:
    """Best-effort harness detection from environment. Returns 'unknown' if unsure."""
    env = os.environ
    if any(env.get(k) for k in (
        "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_SESSION_ID", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR",
    )):
        return "claude-code"
    if any(env.get(k) for k in ("CODEX_HOME", "CODEX_CLI_PATH", "CODEX_SANDBOX")):
        return "codex"
    if any(env.get(k) for k in ("HERMES_HOME", "HERMES_PROFILE")):
        return "hermes"
    return "unknown"


def _record_capture(harness: str, project: Optional[str]) -> None:
    """
    Record a per-harness capture heartbeat to capture-status.json.

    FAIL-OPEN: capture instrumentation must NEVER raise into the spool path.
    Atomic write via os.replace; concurrent racing writers at worst lose a
    heartbeat increment (acceptable for observability).
    """
    try:
        capture_status = _runtime_data_dir() / "capture-status.json"
        data = {}
        if capture_status.exists():
            try:
                data = json.loads(capture_status.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
        rec = data.get(harness, {}) if isinstance(data.get(harness), dict) else {}
        rec["last_ts"] = datetime.now(tz=timezone.utc).isoformat()
        rec["count"] = int(rec.get("count", 0)) + 1
        if project:
            rec["last_project"] = project
        data[harness] = rec
        capture_status.parent.mkdir(parents=True, exist_ok=True)
        tmp = capture_status.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, capture_status)
    except Exception:
        pass  # never break the spool write


def read_capture_status() -> dict:
    """Return the per-harness capture heartbeat map (empty dict if none/unreadable)."""
    try:
        capture_status = _runtime_data_dir() / "capture-status.json"
        if capture_status.exists():
            data = json.loads(capture_status.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Consumer (read + process side)
# ---------------------------------------------------------------------------

class SpoolConsumer:
    """
    Polls spool directory, validates envelopes, ingests records, acks by rename.
    """

    def __init__(self, spool_dir: Optional[Path] = None) -> None:
        self._spool_dir = Path(spool_dir) if spool_dir else _runtime_data_dir() / "spool"
        self._spool_dir.mkdir(parents=True, exist_ok=True)

    def process_pending(self) -> dict:
        """
        Process all .json files in spool dir.
        Returns summary dict: {processed, skipped, failed}.
        """
        from .store.chroma import ChromaDBAdapter
        from .parser import parse_session_file, parse_decisions_file, ParseWarning
        from .decay import DecayScorer
        import warnings

        store = ChromaDBAdapter()
        scorer = DecayScorer()

        summary = {"processed": 0, "skipped": 0, "failed": 0}

        for spool_file in sorted(self._spool_dir.glob("*.json")):
            try:
                result = self._process_one(spool_file, store, scorer)
                if result == "skipped":
                    summary["skipped"] += 1
                else:
                    summary["processed"] += 1
            except Exception as e:
                summary["failed"] += 1
                self._mark_error(spool_file, e)

        return summary

    def _process_one(self, spool_file: Path, store, scorer) -> str:
        envelope = json.loads(spool_file.read_text(encoding="utf-8"))

        # Schema version check
        sv = envelope.get("schema_version", "")
        if sv != _SCHEMA_VERSION:
            raise ValueError(f"Unsupported spool schema_version: {sv}")

        content_hash = _hash_envelope(envelope)
        done_path = self._spool_dir / f"{content_hash}.done"
        if done_path.exists():
            spool_file.unlink(missing_ok=True)
            return "skipped"

        project = envelope.get("project")
        policy_ver = scorer.policy_version_for_record()

        # Collect ALL records from this spool file into one batch, so a record
        # citing a sibling in the same batch resolves regardless of ingest order
        # (Codex PLAN-001: batch-aware provenance).
        records = []
        session_path_str = envelope.get("session_path")
        if session_path_str:
            sp = Path(session_path_str)
            if sp.exists():
                from .parser import parse_session_file
                import warnings
                with warnings.catch_warnings(record=True):
                    records.extend(parse_session_file(sp, project=project))

        for entry in envelope.get("decision_entries", []):
            records.extend(_records_from_decision_entry(entry, session_path_str, project, envelope))

        # Phase 2 — ingest-time provenance validation. QUARANTINE (never reject);
        # fail-open: a validator error must NEVER block ingest (capture contract).
        from .provenance import validate_cites, revalidate_quarantined
        batch_keys = {r.identity_key for r in records}
        for rec in records:
            rec.decay_policy_version = policy_ver
            try:
                rec.provenance_status = validate_cites(rec, store, batch_keys)
            except Exception:
                rec.provenance_status = "valid"  # fail-open
            store.upsert(rec)

        # Re-resolve any previously-quarantined records unblocked by this batch.
        try:
            revalidate_quarantined(store, batch_keys)
        except Exception:
            pass

        # Ack: rename to .done
        spool_file.rename(done_path)
        return "processed"

    def _mark_error(self, spool_file: Path, exc: Exception) -> None:
        """Quarantine a failed envelope and append structured diagnostics."""
        log_dir = self._spool_dir.parent / "logs"
        error_log = log_dir / "spool-errors.jsonl"
        log_dir.mkdir(parents=True, exist_ok=True)
        error_path = _error_path_for_spool(spool_file, self._spool_dir)
        row = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "spool_file": str(spool_file),
            "error_file": str(error_path),
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback_tail": traceback.format_exc(limit=6),
        }
        try:
            with error_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass
        spool_file.rename(error_path)


def _entry_text_and_path(entry: Any, default_path: Optional[str]) -> tuple[str, str]:
    if isinstance(entry, dict):
        text = str(entry.get("text") or "")
        src = str(entry.get("source_file") or default_path or "unknown")
        line = entry.get("source_line")
        if line:
            src = f"{src}:{line}"
        return text, src
    return str(entry), default_path or "unknown"


def _records_from_decision_entry(entry: Any, default_path: Optional[str], project: Optional[str], envelope: dict) -> list:
    from .parser import parse_decision_line
    from .llm_author import author_memories
    from .models import MemoryRecord
    from pathlib import Path as _P

    line, source_path = _entry_text_and_path(entry, default_path)
    if isinstance(entry, dict):
        authored = author_memories(line, source_file=source_path)
        ts = _parse_envelope_timestamp(envelope.get("timestamp"))
        records = []
        for item in authored:
            records.append(
                MemoryRecord(
                    text=item.text,
                    source_uri=source_path,
                    memory_type="decision" if item.kind == "decision" else "inferred",
                    confidence="inferred",
                    timestamp=ts,
                    project=project or _infer_project(_P(_strip_source_line(source_path))),
                    category=item.kind,
                )
            )
        return records

    rec = parse_decision_line(line, _P(source_path), project)
    return [rec] if rec else []


def _parse_envelope_timestamp(value: Optional[str]) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(tz=timezone.utc)


def _strip_source_line(source_path: str) -> str:
    match = re.match(r"^(.*):(\d+)$", source_path)
    return match.group(1) if match else source_path


def _error_path_for_spool(spool_file: Path, spool_dir: Path) -> Path:
    try:
        envelope = json.loads(spool_file.read_text(encoding="utf-8"))
        return spool_dir / f"{_hash_envelope(envelope)}.error"
    except Exception:
        return spool_file.with_suffix(".error")
