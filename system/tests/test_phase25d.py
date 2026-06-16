"""
Phase 2.5-D tests: hash-pinned + typed cites (D1/#3), generation acyclicity
guard (#5), supersession-chain resolution (D2), and legacy backward-compat.
Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

_TMP = tempfile.mkdtemp(prefix="mw-p25d-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter
from memoryweaver.derive import make_derived
from memoryweaver.provenance import resolve_live

_store = ChromaDBAdapter(persist_dir=os.path.join(_TMP, "chroma"))


def _obs(text, proj="p25d", tags=None, thread=None):
    r = MemoryRecord(
        text=text, source_uri="/tmp/o.md", memory_type="session", confidence="explicit",
        timestamp=datetime.now(timezone.utc), project=proj, tags=tags or [], thread=thread,
    )
    _store.upsert(r)
    return r.identity_key


def test_cite_meta_hash_and_predicate():
    # D1: cite captures the source content_hash; #3: typed predicate honored.
    k = _obs("predicate source observation")
    res = make_derived("This refines the source.", cites=[k], project="p25d",
                       predicate="refines", store=_store)
    rec = _store.fetch_by_id(res["identity_key"])
    assert rec.cite_meta[k]["predicate"] == "refines"
    assert rec.cite_meta[k]["content_hash"] == _store.fetch_by_id(k).content_hash


def test_bad_predicate_falls_back_to_cites():
    # #3: closed enum — anything outside {cites,supersedes,refines} -> "cites".
    k = _obs("enum fallback observation")
    res = make_derived("Bogus predicate.", cites=[k], project="p25d",
                       predicate="not-a-real-predicate", store=_store)
    rec = _store.fetch_by_id(res["identity_key"])
    assert rec.cite_meta[k]["predicate"] == "cites"


def test_generation_monotonic():
    # #5: observations are gen 0; a derivation is 1 + max(cited gens).
    o1 = _obs("gen observation A")
    o2 = _obs("gen observation B")
    r1 = make_derived("Derived over two observations.", cites=[o1, o2],
                      project="p25d", store=_store)
    assert r1["generation"] == 1
    r2 = make_derived("Meta-derivation over a gen-1 record.",
                      cites=[r1["identity_key"]], project="p25d", store=_store)
    assert r2["generation"] == 2, "generation strictly increases up the chain"


def test_self_citation_dropped():
    # #5: a record may never cite itself. Predict its id, then try to cite it.
    probe = MemoryRecord(
        text="A conclusion that names itself.", source_uri="derive://manual",
        memory_type="inferred", confidence="inferred",
        timestamp=datetime.now(timezone.utc), project="p25d",
    )
    self_id = probe.identity_key
    res = make_derived("A conclusion that names itself.", cites=[self_id],
                       project="p25d", store=_store)
    assert res["identity_key"] == self_id
    assert res["cites_total"] == 0, "self-citation is stripped by the acyclicity guard"


def test_resolve_live_follows_supersession():
    # D2: a cite to a superseded source can be walked to its live successor.
    v1 = _obs("chain head version one")
    v2 = _obs("chain head version two")
    _store.delete_soft(v1, superseded_by=v2)
    live = resolve_live(_store, v1)
    assert live is not None and live.identity_key == v2
    # superseded source itself still resolves (no retro-quarantine).
    assert _store.fetch_by_id(v1) is not None


def test_resolve_live_cycle_guarded():
    # A malformed superseded_by loop must not hang.
    a = _obs("cycle node a")
    b = _obs("cycle node b")
    _store.delete_soft(a, superseded_by=b)
    _store.delete_soft(b, superseded_by=a)
    got = resolve_live(_store, a, max_hops=8)
    assert got is not None  # returns the last record before the loop, never hangs


def test_legacy_record_reads_back():
    # Backward-compat: a record stored WITHOUT the new keys loads with defaults.
    r = MemoryRecord(
        text="legacy inferred record", source_uri="/tmp/legacy.md",
        memory_type="inferred", confidence="inferred",
        timestamp=datetime.now(timezone.utc), project="p25d", cites=["ghost-key"],
    )
    doc = r.to_chroma_doc()
    del doc["metadata"]["cite_meta"]
    del doc["metadata"]["generation"]
    back = MemoryRecord.from_chroma_result(
        {"id": doc["id"], "document": doc["document"], "metadata": doc["metadata"]}
    )
    assert back.cite_meta == {} and back.generation == 0
    assert back.cites == ["ghost-key"], "legacy cites still round-trip"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} phase-2.5-D tests passed")


if __name__ == "__main__":
    _run_all()
