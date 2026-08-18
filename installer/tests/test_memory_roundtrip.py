from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system"))

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter
from memoryweaver.store.fts import FTS5Adapter


def test_empty_store_write_recall_and_fts(tmp_path):
    store = ChromaDBAdapter(tmp_path / "chroma")
    record = MemoryRecord(
        text="portable memory round trip", source_uri="fixture://memory",
        memory_type="decision", confidence="explicit",
        timestamp=datetime.now(timezone.utc), project="example-project",
    )
    store.upsert(record)
    assert store.fetch_by_id(record.identity_key).text == record.text
    assert store.query_semantic("portable memory")
    fts = FTS5Adapter(tmp_path / "fts.db")
    assert fts.sync_from_chroma(store)["indexed"] == 1
    assert fts.query_bm25("portable")
