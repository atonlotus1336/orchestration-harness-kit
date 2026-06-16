"""
Phase 2.6 revocation tests: revoking a wrong (successorless) source flags its
DIRECT dependents quarantined; supersession (a successor exists) does NOT;
revocation is non-cascading; revoked sources drop out of browse.
Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

_TMP = tempfile.mkdtemp(prefix="mw-revoke-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter
from memoryweaver.derive import make_derived
from memoryweaver.provenance import revoke, validate_cites

_store = ChromaDBAdapter(persist_dir=os.path.join(_TMP, "chroma"))


def _obs(text, proj="rev"):
    r = MemoryRecord(
        text=text, source_uri="/tmp/o.md", memory_type="session", confidence="explicit",
        timestamp=datetime.now(timezone.utc), project=proj,
    )
    _store.upsert(r)
    return r.identity_key


def test_revoke_flags_direct_dependent():
    s = _obs("source that turns out to be wrong")
    d = make_derived("conclusion grounded in the wrong source", cites=[s],
                     project="rev", store=_store)
    assert d["provenance_status"] == "valid"
    res = revoke(_store, s)
    assert res["direct_dependents"] >= 1 and res["flagged_quarantined"] >= 1
    dep = _store.fetch_by_id(d["identity_key"])
    assert dep.provenance_status == "quarantined", "dependent of a revoked source is flagged"


def test_supersession_does_not_quarantine():
    s1 = _obs("decision v1")
    s2 = _obs("decision v2")
    d = make_derived("cites the v1 decision", cites=[s1], project="rev", store=_store)
    # Supersession (a successor exists) — NOT revocation.
    _store.delete_soft(s1, superseded_by=s2)
    dep = _store.fetch_by_id(d["identity_key"])
    assert validate_cites(dep, _store) == "valid", "supersession resolves through to the successor"


def test_revoked_source_hidden_from_browse():
    s = _obs("memory to be revoked and hidden", proj="revhide")
    revoke(_store, s)
    live = _store.query_browse(project="revhide")
    assert all(r.identity_key != s for r in live), "revoked source is tombstoned out of browse"


def test_revocation_non_cascading():
    s = _obs("root source", proj="cascade")
    child = make_derived("child cites root", cites=[s], project="cascade", store=_store)
    grand = make_derived("grandchild cites child", cites=[child["identity_key"]],
                         project="cascade", store=_store)
    revoke(_store, s)
    c = _store.fetch_by_id(child["identity_key"])
    g = _store.fetch_by_id(grand["identity_key"])
    assert c.provenance_status == "quarantined", "direct dependent flagged"
    assert g.provenance_status == "valid", "grandchild NOT cascaded (non-cascading rule)"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} revocation tests passed")


if __name__ == "__main__":
    _run_all()
