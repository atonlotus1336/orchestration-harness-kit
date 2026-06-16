"""
Phase-1 stub-tier tests: stub.project(), fetch_by_id, two-tier retrieval.

Runnable WITHOUT pytest:
    python tests/test_stub_tier.py
Also pytest-compatible (test_* functions).

Sets MEMORYWEAVER_DATA_DIR + MEMORYWEAVER_FTS_DB_PATH to a throwaway temp dir
BEFORE importing memoryweaver (paths/FTS resolve env at import time).
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

_TMP = tempfile.mkdtemp(prefix="mw-stub-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter
from memoryweaver.stub import project, Stub, _first_clause, GIST_VERSION, _GIST_MAX_CHARS


def _rec(text, conf="explicit", mtype="decision", proj="alpha", tags=None, category="decision"):
    return MemoryRecord(
        text=text,
        source_uri="/tmp/session.md",
        memory_type=mtype,
        confidence=conf,
        timestamp=datetime.now(timezone.utc),
        project=proj,
        tags=tags if tags is not None else ["x", "y"],
        category=category,
    )


def test_gist_deterministic_and_bounded():
    long = "First clause here. " + ("padding words " * 60)
    assert _first_clause(long) == _first_clause(long), "gist must be deterministic"
    g = _first_clause(long)
    assert len(g) <= _GIST_MAX_CHARS + 1, "gist must be bounded"
    assert g.startswith("First clause here"), "gist starts at the real stored text (non-generative)"
    assert _first_clause("hi there") == "hi there"   # short text passes through
    assert _first_clause("") == ""


def test_project_from_record():
    s = project(_rec("A decided thing. Extra detail.", conf="inferred", tags=["alpha", "beta"]))
    assert isinstance(s, Stub)
    assert s.confidence == "inferred"          # observation/derived axis
    assert s.memory_type == "decision"
    assert s.tags == ["alpha", "beta"]
    assert s.gist.startswith("A decided thing")
    assert s.gist_version == GIST_VERSION
    assert s.identity_key


def test_project_malformed_dict_safe_defaults():
    s = project({"id": "k1", "metadata": {}, "document": ""})
    assert s.identity_key == "k1"
    assert s.confidence == "unknown"
    assert s.tags == []
    assert s.gist == ""


def test_fetch_by_id_and_two_tier():
    store = ChromaDBAdapter(persist_dir=os.path.join(_TMP, "chroma"))
    r_exp = _rec("Explicit memory about auth token design.", conf="explicit")
    r_inf = _rec("Inferred pattern about request retries.", conf="inferred")
    store.upsert(r_exp)
    store.upsert(r_inf)

    got_e = store.fetch_by_id(r_exp.identity_key)
    got_i = store.fetch_by_id(r_inf.identity_key)
    assert got_e is not None and got_e.text == r_exp.text
    assert got_i is not None and got_i.confidence == "inferred"
    assert store.fetch_by_id("does-not-exist") is None

    results = store.query_semantic("auth token design", top_k=5)
    assert results, "semantic query returns the explicit memory"
    stub = project(results[0])
    assert stub.identity_key and stub.gist
    assert len(stub.gist) <= _GIST_MAX_CHARS + 1
    # stub round-trips to full detail via fetch_by_id
    full = store.fetch_by_id(stub.identity_key)
    assert full is not None and len(full.text) >= len(stub.gist)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} stub-tier tests passed")


if __name__ == "__main__":
    _run_all()
