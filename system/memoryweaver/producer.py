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
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ._paths import MW_DATA_DIR

_SPOOL_DIR = MW_DATA_DIR / "spool"
_SCHEMA_VERSION = "1.0"
_CAPTURE_STATUS = MW_DATA_DIR / "capture-status.json"


# ---------------------------------------------------------------------------
# Producer (write side)
# ---------------------------------------------------------------------------

def write_spool(
    session_path: Optional[Path] = None,
    decisions_path: Optional[Path] = None,
    project: Optional[str] = None,
    thread: Optional[str] = None,
    harness: Optional[str] = None,
) -> Path:
    """
    Write a MemoryEnvelope spool file. Returns path to the .json file.
    Idempotent: if .done file for same content hash exists, returns that path.

    `harness` records which agent harness captured this (claude-code|codex|hermes);
    inferred from env if omitted. Used for the per-harness capture heartbeat so
    cross-harness capture parity is observable via `memory_status`.
    """
    _SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    if harness is None:
        harness = _infer_harness()

    session_id = (
        session_path.stem if session_path else datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    )
    timestamp = datetime.now(tz=timezone.utc).isoformat()

    decision_entries: List[str] = []
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
    done_path = _SPOOL_DIR / f"{content_hash}.done"
    if done_path.exists():
        return done_path  # idempotent — already processed

    ts_prefix = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    spool_path = _SPOOL_DIR / f"{ts_prefix}-{content_hash[:12]}.json"
    spool_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    _record_capture(harness, project)  # fail-open per-harness heartbeat (Phase 3)
    return spool_path


def _read_recent_decisions(path: Path) -> List[str]:
    """Return decision lines added since last session (heuristic: last 20 lines)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    # Filter to non-empty, non-header lines
    entries = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
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
        data = {}
        if _CAPTURE_STATUS.exists():
            try:
                data = json.loads(_CAPTURE_STATUS.read_text(encoding="utf-8"))
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
        tmp = _CAPTURE_STATUS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, _CAPTURE_STATUS)
    except Exception:
        pass  # never break the spool write


def read_capture_status() -> dict:
    """Return the per-harness capture heartbeat map (empty dict if none/unreadable)."""
    try:
        if _CAPTURE_STATUS.exists():
            data = json.loads(_CAPTURE_STATUS.read_text(encoding="utf-8"))
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
        self._spool_dir = Path(spool_dir) if spool_dir else _SPOOL_DIR
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
                # Rename to .error so we don't retry indefinitely
                spool_file.rename(spool_file.with_suffix(".error"))

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

        for line in envelope.get("decision_entries", []):
            from .parser import parse_decision_line
            from pathlib import Path as _P
            rec = parse_decision_line(line, _P(session_path_str or "unknown"), project)
            if rec:
                records.append(rec)

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
