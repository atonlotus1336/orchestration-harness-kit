"""SQLite FTS5 index used by the portable MemoryWeaver core."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .._paths import MW_DATA_DIR


class FTS5Adapter:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or os.environ.get("MEMORYWEAVER_FTS_DB_PATH", MW_DATA_DIR / "fts.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(identity_key UNINDEXED, text)")

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0])

    def sync_from_chroma(self, store) -> dict:
        records = store.query_browse(confidence="all")
        with self._connect() as db:
            db.execute("DELETE FROM memories_fts")
            db.executemany(
                "INSERT INTO memories_fts(identity_key, text) VALUES(?, ?)",
                [(r.identity_key, r.text) for r in records],
            )
        return {"indexed": len(records), "status": "PASS"}

    def search(self, text: str, top_k: int = 10) -> list[dict]:
        terms = " OR ".join(part for part in text.replace('"', " ").split() if part)
        if not terms:
            return []
        with self._connect() as db:
            rows = db.execute(
                "SELECT identity_key, bm25(memories_fts) AS rank FROM memories_fts "
                "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?", (terms, int(top_k))
            ).fetchall()
        return [{"id": row[0], "score": 1.0 / (1.0 + abs(float(row[1])))} for row in rows]

    def query_bm25(self, text: str, top_k: int = 10) -> list[dict]:
        return self.search(text, top_k)
