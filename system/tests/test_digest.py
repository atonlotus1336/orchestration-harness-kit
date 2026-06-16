"""
Phase 4 digest tests: the recent-memory snapshot is written deterministically,
ordered newest-first, excludes quarantined records, and round-trips. No pytest.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP = tempfile.mkdtemp(prefix="mw-digest-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter
from memoryweaver.derive import make_derived
from memoryweaver.digest import write_recent_digest, read_recent_digest

_store = ChromaDBAdapter(persist_dir=os.path.join(_TMP, "chroma"))
_DIGEST = os.path.join(_TMP, "recent-digest.json")
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _obs(text, when, proj="dg"):
    r = MemoryRecord(
        text=text, source_uri="/tmp/o.md", memory_type="session", confidence="explicit",
        timestamp=when, project=proj,
    )
    _store.upsert(r)
    return r.identity_key


def test_digest_newest_first_and_bounded():
    for i in range(5):
        _obs(f"memory number {i}", _BASE + timedelta(hours=i))
    payload = write_recent_digest(store=_store, n=3, path=_DIGEST)
    assert payload["count"] == 3
    gists = [s["gist"] for s in payload["stubs"]]
    assert "memory number 4" in gists[0], "newest first"
    assert all("number 0" not in g and "number 1" not in g for g in gists), "older trimmed"


def test_digest_excludes_quarantined():
    res = make_derived("derived from a missing source", cites=["no-such-key"],
                       project="dg", store=_store)
    assert res["provenance_status"] == "quarantined"
    payload = write_recent_digest(store=_store, n=20, path=_DIGEST)
    ids = [s["identity_key"] for s in payload["stubs"]]
    assert res["identity_key"] not in ids, "quarantined memory is not surfaced as recent context"


def test_digest_roundtrips_to_disk():
    write_recent_digest(store=_store, n=4, path=_DIGEST)
    back = read_recent_digest(path=_DIGEST)
    assert back is not None and back["schema"] == "recent-digest/1"
    assert isinstance(back["stubs"], list) and len(back["stubs"]) <= 4
    assert all("gist" in s and "identity_key" in s for s in back["stubs"])


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} digest tests passed")


if __name__ == "__main__":
    _run_all()
