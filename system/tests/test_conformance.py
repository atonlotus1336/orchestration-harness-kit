"""
VectorStore conformance tests — prerequisite for any future backend addition (fixes E-005).

Run: python -m pytest tests/test_conformance.py -v
"""

import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter


@pytest.fixture
def tmp_store(tmp_path):
    """Fresh ChromaDB store in a temp directory."""
    return ChromaDBAdapter(persist_dir=tmp_path / "chroma")


def make_record(
    text="test memory",
    confidence="explicit",
    project="TestProject",
    memory_type="session",
    source_uri="/tmp/test.md",
    **kwargs,
) -> MemoryRecord:
    return MemoryRecord(
        text=text,
        source_uri=source_uri,
        memory_type=memory_type,
        confidence=confidence,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        project=project,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# upsert + count
# ---------------------------------------------------------------------------

class TestUpsert:
    def test_upsert_explicit_increases_count(self, tmp_store):
        assert tmp_store.count("explicit_memories") == 0
        tmp_store.upsert(make_record(text="hello explicit", confidence="explicit"))
        assert tmp_store.count("explicit_memories") == 1

    def test_upsert_inferred_goes_to_inferred_collection(self, tmp_store):
        tmp_store.upsert(make_record(text="hello inferred", confidence="inferred"))
        assert tmp_store.count("inferred_memories") == 1
        assert tmp_store.count("explicit_memories") == 0

    def test_upsert_idempotent_on_same_identity_key(self, tmp_store):
        rec = make_record(text="same content", confidence="explicit")
        tmp_store.upsert(rec)
        tmp_store.upsert(rec)  # same identity_key
        assert tmp_store.count("explicit_memories") == 1

    def test_upsert_different_source_uri_creates_separate_record(self, tmp_store):
        rec1 = make_record(text="same content", confidence="explicit")
        rec2 = MemoryRecord(
            text="same content",
            source_uri="/different/path.md",
            memory_type="session",
            confidence="explicit",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            project="TestProject",
        )
        tmp_store.upsert(rec1)
        tmp_store.upsert(rec2)
        assert tmp_store.count("explicit_memories") == 2


# ---------------------------------------------------------------------------
# query_semantic
# ---------------------------------------------------------------------------

class TestQuerySemantic:
    def test_returns_explicit_by_default(self, tmp_store):
        tmp_store.upsert(make_record(text="Python asyncio event loop", confidence="explicit"))
        tmp_store.upsert(make_record(text="Python asyncio event loop", confidence="inferred"))
        results = tmp_store.query_semantic("Python async", top_k=5)
        confidences = [r["metadata"]["confidence"] for r in results]
        assert all(c == "explicit" for c in confidences)

    def test_include_inferred_returns_both(self, tmp_store):
        tmp_store.upsert(make_record(text="async pattern", confidence="explicit"))
        tmp_store.upsert(make_record(
            text="async pattern",
            confidence="inferred",
            source_uri="/other/path.md",
        ))
        results = tmp_store.query_semantic("async", top_k=10, include_inferred=True)
        confidences = {r["metadata"]["confidence"] for r in results}
        assert "explicit" in confidences
        assert "inferred" in confidences

    def test_project_filter_isolates_results(self, tmp_store):
        tmp_store.upsert(make_record(text="auth system", confidence="explicit", project="ProjectA"))
        tmp_store.upsert(make_record(
            text="auth system",
            confidence="explicit",
            project="ProjectB",
            source_uri="/other.md",
        ))
        results = tmp_store.query_semantic("auth", top_k=10, project="ProjectA")
        projects = [r["metadata"]["project"] for r in results]
        assert all(p == "ProjectA" for p in projects)

    def test_superseded_records_excluded(self, tmp_store):
        rec = make_record(text="old memory to supersede", confidence="explicit")
        tmp_store.upsert(rec)
        tmp_store.delete_soft(rec.identity_key)
        results = tmp_store.query_semantic("old memory", top_k=5)
        assert len(results) == 0

    def test_scores_between_0_and_1(self, tmp_store):
        tmp_store.upsert(make_record(text="machine learning model", confidence="explicit"))
        results = tmp_store.query_semantic("machine learning", top_k=5)
        for r in results:
            assert 0.0 <= r["score"] <= 1.0

    def test_empty_store_returns_empty(self, tmp_store):
        results = tmp_store.query_semantic("anything", top_k=5)
        assert results == []


# ---------------------------------------------------------------------------
# query_browse
# ---------------------------------------------------------------------------

class TestQueryBrowse:
    def test_browse_project_scoped(self, tmp_store):
        tmp_store.upsert(make_record(text="p1 memory", confidence="explicit", project="P1"))
        tmp_store.upsert(make_record(
            text="p2 memory",
            confidence="explicit",
            project="P2",
            source_uri="/p2.md",
        ))
        results = tmp_store.query_browse(project="P1")
        assert len(results) == 1
        assert results[0].project == "P1"

    def test_browse_confidence_all(self, tmp_store):
        tmp_store.upsert(make_record(text="explicit rec", confidence="explicit"))
        tmp_store.upsert(make_record(
            text="inferred rec",
            confidence="inferred",
            source_uri="/inferred.md",
        ))
        results = tmp_store.query_browse(confidence="all")
        confidences = {r.confidence for r in results}
        assert "explicit" in confidences
        assert "inferred" in confidences

    def test_browse_excludes_superseded(self, tmp_store):
        rec = make_record(text="superseded browse record", confidence="explicit")
        tmp_store.upsert(rec)
        tmp_store.delete_soft(rec.identity_key)
        results = tmp_store.query_browse(project="TestProject")
        assert not any(r.superseded for r in results)

    def test_browse_no_decay_applied(self, tmp_store):
        """Browse results must not have decay_score — deterministic only."""
        tmp_store.upsert(make_record(text="deterministic browse", confidence="explicit"))
        results = tmp_store.query_browse()
        # Results are MemoryRecord objects, not score dicts
        assert all(isinstance(r, MemoryRecord) for r in results)


# ---------------------------------------------------------------------------
# delete_soft
# ---------------------------------------------------------------------------

class TestDeleteSoft:
    def test_soft_delete_marks_superseded(self, tmp_store):
        rec = make_record(text="to tombstone", confidence="explicit")
        tmp_store.upsert(rec)
        tmp_store.delete_soft(rec.identity_key, superseded_by="new-key-123")
        # Record still exists but is superseded
        results = tmp_store.query_semantic("tombstone", top_k=5)
        assert len(results) == 0  # filtered by superseded=false in semantic query

    def test_soft_delete_nonexistent_key_does_not_crash(self, tmp_store):
        tmp_store.delete_soft("nonexistent-key-abc123")  # must not raise


# ---------------------------------------------------------------------------
# MemoryRecord identity
# ---------------------------------------------------------------------------

class TestMemoryRecordIdentity:
    def test_same_text_same_uri_same_type_same_scope_same_key(self):
        r1 = make_record(text="hello", memory_type="session", project="P")
        r2 = make_record(text="hello", memory_type="session", project="P")
        assert r1.identity_key == r2.identity_key

    def test_same_text_different_source_uri_different_key(self):
        r1 = make_record(text="hello")
        r2 = MemoryRecord(
            text="hello",
            source_uri="/different.md",
            memory_type="session",
            confidence="explicit",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            project="TestProject",
        )
        assert r1.identity_key != r2.identity_key

    def test_same_text_different_project_different_key(self):
        r1 = make_record(text="hello", project="A")
        r2 = make_record(text="hello", project="B")
        assert r1.identity_key != r2.identity_key

    def test_same_text_different_memory_type_different_key(self):
        r1 = make_record(text="hello", memory_type="session")
        r2 = make_record(text="hello", memory_type="decision")
        assert r1.identity_key != r2.identity_key
