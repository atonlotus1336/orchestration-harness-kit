"""
Phase-2 end-to-end ingest smoke: exercises the REAL capture path
(write_spool -> SpoolConsumer.process_pending) through the modified
provenance-validating _process_one. Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="mw-ingest-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.producer import write_spool, SpoolConsumer
from memoryweaver.store.chroma import ChromaDBAdapter


def test_ingest_roundtrip_sets_provenance():
    sess = Path(_TMP) / "2026-06-15-smoke.md"
    sess.write_text(
        "## Decision\nWe chose the zero-drift projection approach for stubs.\n",
        encoding="utf-8",
    )
    write_spool(session_path=sess, project="smoke")

    summary = SpoolConsumer().process_pending()
    assert summary["processed"] >= 1, f"ingest did not process the spool: {summary}"
    assert summary["failed"] == 0, f"ingest failed: {summary}"

    store = ChromaDBAdapter()  # default persist dir == MW_DATA_DIR/chroma (same as consumer)
    total = store.count("explicit_memories") + store.count("inferred_memories")
    assert total >= 1, "no records landed in the store"

    recs = store.query_browse(confidence="all")
    assert recs, "browse returned nothing"
    # Every record carries a provenance_status; un-cited records are valid.
    assert all(r.provenance_status in ("valid", "quarantined") for r in recs)
    assert any(r.provenance_status == "valid" for r in recs)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} ingest-smoke tests passed")


if __name__ == "__main__":
    _run_all()
