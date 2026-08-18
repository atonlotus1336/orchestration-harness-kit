"""Portable persistent adapter with the historical ChromaDB interface.

The client kit must work offline and without a model download.  This adapter
therefore stores canonical records in SQLite and uses deterministic token
overlap for the semantic seam.  The public class name is retained so existing
harness bindings and migrations remain compatible.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .._paths import MW_DATA_DIR
from ..models import MemoryRecord

_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN.finditer(text or "")}


class _Collection:
    """Small compatibility facade used by legacy migration/provenance code."""

    def __init__(self, adapter: "ChromaDBAdapter", confidence: str) -> None:
        self.adapter = adapter
        self.confidence = confidence

    def count(self) -> int:
        return self.adapter.count(f"{self.confidence}_memories", include_superseded=True)

    def get(self, ids=None, where=None, include=None, limit=None):
        records = self.adapter._records(self.confidence, include_superseded=True)
        if ids is not None:
            wanted = set(ids)
            records = [r for r in records if r.identity_key in wanted]
        if where:
            for key, condition in where.items():
                expected = condition.get("$eq") if isinstance(condition, dict) else condition
                records = [r for r in records if str(getattr(r, key, "")) == str(expected)]
        if limit is not None:
            records = records[: int(limit)]
        docs = [r.to_chroma_doc() for r in records]
        return {
            "ids": [d["id"] for d in docs],
            "documents": [d["document"] for d in docs],
            "metadatas": [d["metadata"] for d in docs],
        }

    def update(self, ids, metadatas, documents=None):
        for index, identity_key in enumerate(ids):
            current = self.adapter.fetch_by_id(identity_key)
            if current is None:
                continue
            doc = {
                "id": identity_key,
                "document": (documents or [current.text])[index],
                "metadata": metadatas[index],
            }
            self.adapter.upsert(MemoryRecord.from_chroma_result(doc))


class ChromaDBAdapter:
    """Persistent MemoryRecord store with the established adapter contract."""

    def __init__(self, persist_dir: Optional[Path | str] = None) -> None:
        root = Path(persist_dir) if persist_dir else MW_DATA_DIR / "chroma"
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "memoryweaver.sqlite3"
        self._explicit = _Collection(self, "explicit")
        self._inferred = _Collection(self, "inferred")
        self._get_client()

    def _get_client(self):
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "identity_key TEXT PRIMARY KEY, confidence TEXT NOT NULL, "
                "document TEXT NOT NULL, metadata TEXT NOT NULL)"
            )
        return self

    def _connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def upsert(self, record: MemoryRecord) -> None:
        doc = record.to_chroma_doc()
        with self._connect() as db:
            db.execute(
                "INSERT INTO memories(identity_key, confidence, document, metadata) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(identity_key) DO UPDATE SET "
                "confidence=excluded.confidence, document=excluded.document, metadata=excluded.metadata",
                (record.identity_key, record.confidence, record.text, json.dumps(doc["metadata"], sort_keys=True)),
            )

    def fetch_by_id(self, identity_key: str) -> Optional[MemoryRecord]:
        with self._connect() as db:
            row = db.execute(
                "SELECT identity_key, document, metadata FROM memories WHERE identity_key=?", (identity_key,)
            ).fetchone()
        return self._row_record(row) if row else None

    def delete_soft(self, identity_key: str, superseded_by: Optional[str] = None, revoked: bool = False) -> None:
        record = self.fetch_by_id(identity_key)
        if record is None:
            return
        record.superseded = True
        record.superseded_by = superseded_by
        record.revoked = revoked
        self.upsert(record)

    def count(self, collection: str, include_superseded: bool = False) -> int:
        confidence = "inferred" if collection.startswith("inferred") else "explicit"
        records = self._records(confidence, include_superseded=True)
        return len(records) if include_superseded else sum(not r.superseded for r in records)

    def query_browse(self, confidence="explicit", wing=None, room=None, drawer=None,
                     tag=None, project=None, thread=None, limit=None):
        confidences = ("explicit", "inferred") if confidence == "all" else (confidence,)
        records = []
        for value in confidences:
            records.extend(self._records(value, include_superseded=False))
        def keep(r):
            return (
                (not wing or r.wing == wing) and (not room or r.room == room)
                and (not drawer or r.drawer == drawer) and (not project or r.project == project)
                and (not thread or r.thread == thread) and (not tag or tag in r.tags)
            )
        records = [r for r in records if keep(r)]
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records[: int(limit)] if limit else records

    def query_semantic(self, text: str, top_k: int = 10, project=None, thread=None,
                       include_inferred: bool = False):
        records = self.query_browse(
            confidence="all" if include_inferred else "explicit", project=project, thread=thread
        )
        query = _tokens(text)
        results = []
        for record in records:
            terms = _tokens(record.text)
            overlap = len(query & terms)
            score = overlap / math.sqrt(max(len(query), 1) * max(len(terms), 1))
            if overlap or not query:
                doc = record.to_chroma_doc()
                results.append({**doc, "score": max(0.0, min(1.0, score))})
        results.sort(key=lambda item: (item["score"], item["id"]), reverse=True)
        return results[: int(top_k)]

    def query_hybrid(self, text: str, top_k: int = 10, project=None, thread=None,
                     include_inferred: bool = False, rrf_k: int = 60):
        semantic = self.query_semantic(text, top_k=max(top_k * 2, 20), project=project,
                                       thread=thread, include_inferred=include_inferred)
        try:
            from .fts import FTS5Adapter
            from .hybrid import rrf_merge
            lexical = FTS5Adapter().query_bm25(text, top_k=max(top_k * 2, 20))
            full = {item["id"]: item for item in semantic}
            for hit in lexical:
                if hit["id"] not in full:
                    record = self.fetch_by_id(hit["id"])
                    if record and (include_inferred or record.confidence == "explicit"):
                        full[hit["id"]] = {**record.to_chroma_doc(), "score": hit["score"]}
            lexical_full = [full[item["id"]] for item in lexical if item["id"] in full]
            return rrf_merge(semantic, lexical_full, k=rrf_k, top_k=top_k)
        except Exception:
            for rank, item in enumerate(semantic[:top_k], start=1):
                item["rrf_score"] = 1.0 / (rrf_k + rank)
            return semantic[:top_k]

    def _records(self, confidence: str, include_superseded: bool) -> list[MemoryRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT identity_key, document, metadata FROM memories WHERE confidence=?", (confidence,)
            ).fetchall()
        records = [self._row_record(row) for row in rows]
        return records if include_superseded else [r for r in records if not r.superseded]

    @staticmethod
    def _row_record(row) -> MemoryRecord:
        return MemoryRecord.from_chroma_result({
            "id": row["identity_key"], "document": row["document"],
            "metadata": json.loads(row["metadata"]),
        })
