"""
Phase-2.5 derivation-workflow tests: the make_derived seam (manual A) +
synthesize rollups (B tag + D thread). Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

_TMP = tempfile.mkdtemp(prefix="mw-derive-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter
from memoryweaver.derive import make_derived, synthesize, _all_source_records
from memoryweaver.stub import project

_store = ChromaDBAdapter(persist_dir=os.path.join(_TMP, "chroma"))


def _obs(text, tags=None, thread=None, proj="alpha"):
    r = MemoryRecord(
        text=text, source_uri="/tmp/o.md", memory_type="session", confidence="explicit",
        timestamp=datetime.now(timezone.utc), project=proj, tags=tags or [], thread=thread,
    )
    _store.upsert(r)
    return r.identity_key


def test_manual_derive_valid():
    k = _obs("observation one about auth tokens")
    res = make_derived("Concluded auth uses bearer tokens.", cites=[k], project="alpha", store=_store)
    assert res["provenance_status"] == "valid"
    assert res["cites_resolved"] == 1 and res["cites_total"] == 1
    rec = _store.fetch_by_id(res["identity_key"])
    assert rec.confidence == "inferred" and rec.cites == [k]
    assert any(t.startswith("derivation:") for t in rec.tags)
    assert project(rec).provenance_status == "valid"


def test_manual_derive_unresolved_quarantined():
    res = make_derived("Derived from nothing.", cites=["no-such-key"], store=_store)
    assert res["provenance_status"] == "quarantined"


def test_no_rollup_of_rollups():
    # derivations (from the manual tests above) must be excluded as sources.
    srcs = _all_source_records(_store)
    assert all(not any(t.startswith("derivation:") for t in (s.tags or [])) for s in srcs)


def test_synthesize_tag_rollup():
    for i in range(3):
        _obs(f"tagged memory number {i}", tags=["shared-topic"], proj="beta")
    res = synthesize(store=_store, strategies=["tag"])
    assert res["rollups_created"] >= 1
    rollups = [r for r in _store.query_browse(confidence="all")
               if "rollup:tag" in (r.tags or []) and not r.superseded]
    big = [r for r in rollups if len(r.cites) >= 3]
    assert big, "a tag rollup cites its >=3 members"
    assert big[0].provenance_status == "valid"
    assert "Index" in big[0].text


def test_synthesize_thread_rollup_idempotent():
    for i in range(3):
        _obs(f"thread memory number {i}", thread="proj-thread", proj="gamma")
    synthesize(store=_store, strategies=["thread"])

    def live_rollups():
        return [r for r in _store.query_browse(confidence="all")
                if r.source_uri.startswith("derive://rollup/thread/proj-thread") and not r.superseded]

    before = len(live_rollups())
    assert before == 1
    synthesize(store=_store, strategies=["thread"])  # re-run
    after = len(live_rollups())
    assert after == 1, "re-synthesize supersedes the prior rollup, never duplicates"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} derive tests passed")


if __name__ == "__main__":
    _run_all()
