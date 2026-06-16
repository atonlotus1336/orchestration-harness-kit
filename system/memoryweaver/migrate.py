"""
MigrationRunner — idempotent batch ingest of existing session + decision files.

Identity key: content_hash + source_uri + memory_type + project_scope
Tombstones on hash collision if newer timestamp (preserves old as superseded=true).
Writes migration.log append-only with per-file status.

Usage:
  python -m memoryweaver migrate
  python -m memoryweaver migrate --sessions ~/.claude/memory/sessions/
  python -m memoryweaver migrate --decisions ~/notes/decisions/
"""

from __future__ import annotations

import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ._paths import MW_DATA_DIR

_DEFAULT_SESSIONS_DIR = Path.home() / ".claude" / "Memory" / "sessions"
_DEFAULT_LOG_PATH = MW_DATA_DIR / "migration.log"
_SCHEMA_VERSION = "1.0"
_SCHEMA_TABLE_PATH = MW_DATA_DIR / "migration_schema.txt"


class MigrationRunner:
    """Idempotent batch ingest for existing memory files."""

    def __init__(
        self,
        sessions_dir: Optional[Path] = None,
        projects_dir: Optional[Path] = None,
        log_path: Optional[Path] = None,
    ) -> None:
        self._sessions_dir = Path(sessions_dir) if sessions_dir else _DEFAULT_SESSIONS_DIR
        self._projects_dir = Path(projects_dir) if projects_dir else None
        self._log_path = Path(log_path) if log_path else _DEFAULT_LOG_PATH
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def run(self) -> dict:
        """
        Run migration. Returns summary:
          {ingested, duplicate, superseded, parse_warning, failed, skipped}
        """
        if self._already_run():
            return {"skipped": True, "reason": f"schema_version={_SCHEMA_VERSION} already migrated"}

        from .store.chroma import ChromaDBAdapter
        from .parser import parse_session_file, parse_decisions_file, ParseWarning
        from .decay import DecayScorer

        store = ChromaDBAdapter()
        scorer = DecayScorer()
        policy_ver = scorer.policy_version_for_record()

        summary = {"ingested": 0, "duplicate": 0, "superseded": 0,
                   "parse_warning": 0, "failed": 0}

        # Phase A — existing sessions/*.md
        if self._sessions_dir.exists():
            for md_file in sorted(self._sessions_dir.glob("*.md")):
                self._ingest_file(md_file, "session", store, scorer, policy_ver, summary)

        # Phase B — Projects/*/Memory/decisions-*.md
        if self._projects_dir and self._projects_dir.exists():
            for decisions_file in sorted(self._projects_dir.rglob("decisions-*.md")):
                self._ingest_file(decisions_file, "decision", store, scorer, policy_ver, summary)

        # Phase C — global ~/.claude/DECISIONS.md
        global_decisions = Path.home() / ".claude" / "DECISIONS.md"
        if global_decisions.exists():
            self._ingest_file(global_decisions, "decision", store, scorer, policy_ver, summary)

        self._write_schema_version()
        self._append_log(f"MIGRATION COMPLETE schema_version={_SCHEMA_VERSION} "
                         f"summary={summary}")
        return summary

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _ingest_file(
        self, path: Path, file_type: str, store, scorer, policy_ver: str, summary: dict
    ) -> None:
        from .parser import parse_session_file, parse_decisions_file, ParseWarning

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ParseWarning)
                if file_type == "decision":
                    records = parse_decisions_file(path)
                else:
                    records = parse_session_file(path)

            parse_warned = any(issubclass(w.category, ParseWarning) for w in caught)
            if parse_warned:
                summary["parse_warning"] += 1
                self._append_log(f"parse-warning\t{path}")

        except Exception as e:
            summary["failed"] += 1
            self._append_log(f"failed\t{path}\t{e}")
            return

        for rec in records:
            rec.decay_policy_version = policy_ver
            try:
                # Check for existing record with same identity_key
                existing = self._find_existing(store, rec.identity_key, rec.confidence)
                if existing is None:
                    store.upsert(rec)
                    summary["ingested"] += 1
                    self._append_log(f"ingested\t{path}\t{rec.identity_key[:16]}")
                else:
                    # Check timestamp: if new record is newer, tombstone old
                    old_ts = existing.timestamp
                    new_ts = rec.timestamp
                    if new_ts > old_ts:
                        store.delete_soft(rec.identity_key, superseded_by=rec.identity_key)
                        store.upsert(rec)
                        summary["superseded"] += 1
                        self._append_log(f"superseded\t{path}\t{rec.identity_key[:16]}")
                    else:
                        summary["duplicate"] += 1
                        self._append_log(f"duplicate\t{path}\t{rec.identity_key[:16]}")
            except Exception as e:
                summary["failed"] += 1
                self._append_log(f"failed\t{path}\tupsert error: {e}")

    def _find_existing(self, store, identity_key: str, confidence: str):
        """
        Try to fetch existing record by identity_key.
        ELT-003 fix: check BOTH collections — a record may have been ingested
        with a different confidence value than the current record.
        """
        from .store.chroma import ChromaDBAdapter
        from .models import MemoryRecord
        if not isinstance(store, ChromaDBAdapter):
            return None
        store._get_client()
        for col in (store._explicit, store._inferred):
            try:
                r = col.get(ids=[identity_key], include=["documents", "metadatas"])
                if r["ids"]:
                    return MemoryRecord.from_chroma_result({
                        "id": r["ids"][0],
                        "document": r["documents"][0],
                        "metadata": r["metadatas"][0],
                    })
            except Exception:
                continue
        return None

    def _append_log(self, line: str) -> None:
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{line}\n")

    def _already_run(self) -> bool:
        if not _SCHEMA_TABLE_PATH.exists():
            return False
        return _SCHEMA_VERSION in _SCHEMA_TABLE_PATH.read_text(encoding="utf-8")

    def _write_schema_version(self) -> None:
        _SCHEMA_TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if _SCHEMA_TABLE_PATH.exists():
            existing = _SCHEMA_TABLE_PATH.read_text(encoding="utf-8")
        if _SCHEMA_VERSION not in existing:
            with open(_SCHEMA_TABLE_PATH, "a", encoding="utf-8") as f:
                f.write(f"{_SCHEMA_VERSION}\n")
