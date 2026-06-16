"""
Phase 2.7 link-harvest tests: extract [[wikilinks]]/supersedes as typed
candidates, resolve deterministically against existing memories, route through
the make_derived hash-gate (any unresolved target -> quarantined), bounded.
Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

_TMP = tempfile.mkdtemp(prefix="mw-links-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter
from memoryweaver.links import extract_candidates, harvest, _MAX_CANDIDATES

_store = ChromaDBAdapter(persist_dir=os.path.join(_TMP, "chroma"))


def _decision(name, rationale, proj, uri):
    r = MemoryRecord(
        text=f"[2026-01-01] — {name}: {rationale}", source_uri=uri,
        memory_type="decision", confidence="explicit",
        timestamp=datetime.now(timezone.utc), project=proj,
    )
    _store.upsert(r)
    return r.identity_key


def test_extract_wikilinks_and_supersedes():
    cands = extract_candidates(
        "This decision supersedes [[Old Choice]]. See also [[Related Note]] and [[plain|alias]]."
    )
    preds = {c["target"]: c["predicate"] for c in cands}
    assert preds.get("Old Choice") == "supersedes"
    assert preds.get("Related Note") == "refines"
    assert preds.get("plain") == "refines", "alias form [[target|alias]] captures the target"


def test_harvest_resolves_known_and_quarantines_unknown():
    k = _decision("Auth Decision", "use bearer tokens", "hlink", "/d/auth.md")
    res = harvest(
        "New note refines [[Auth Decision]] and [[Totally Unknown Thing]].",
        project="hlink", derived_text="note about auth", store=_store,
    )
    assert res["resolved"] == 1 and res["unresolved"] == 1
    assert res["provenance_status"] == "quarantined", "any unresolved target -> quarantined"
    rec = _store.fetch_by_id(res["identity_key"])
    assert k in rec.cites
    assert rec.cite_meta[k]["predicate"] == "refines"
    assert rec.cite_meta[k]["content_hash"] == _store.fetch_by_id(k).content_hash


def test_harvest_all_resolved_is_valid():
    k = _decision("Cache Decision", "use redis", "hlink2", "/d/cache.md")
    res = harvest(
        "supersedes [[Cache Decision]]", project="hlink2",
        derived_text="new cache decision", store=_store,
    )
    assert res["resolved"] == 1 and res["unresolved"] == 0
    assert res["provenance_status"] == "valid"
    rec = _store.fetch_by_id(res["identity_key"])
    assert rec.cite_meta[k]["predicate"] == "supersedes"


def test_harvest_no_links_is_noop():
    res = harvest("A plain note with no links at all.", project="hlink3", store=_store)
    assert res["candidates"] == 0 and res["identity_key"] is None


def test_harvest_bounded_and_reports_drops():
    text = " ".join(f"[[Note {i}]]" for i in range(_MAX_CANDIDATES + 8))
    res = harvest(text, project="hlinkb", derived_text="many links", store=_store)
    assert res["candidates"] == _MAX_CANDIDATES
    assert res["dropped"] == 8 and len(res["dropped_targets"]) == 8


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} link-harvest tests passed")


if __name__ == "__main__":
    _run_all()
