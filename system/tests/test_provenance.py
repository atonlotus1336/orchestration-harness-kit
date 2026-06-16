"""
Phase-2 provenance tests: cites schema round-trip, ingest-time quarantine,
and cross-batch revalidation. Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

_TMP = tempfile.mkdtemp(prefix="mw-prov-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.models import MemoryRecord
from memoryweaver.store.chroma import ChromaDBAdapter
from memoryweaver.provenance import validate_cites, revalidate_quarantined
from memoryweaver.stub import project

_store = ChromaDBAdapter(persist_dir=os.path.join(_TMP, "chroma"))


def _rec(text, conf="explicit", mtype="decision", cites=None, proj="alpha"):
    return MemoryRecord(
        text=text, source_uri="/tmp/s.md", memory_type=mtype, confidence=conf,
        timestamp=datetime.now(timezone.utc), project=proj, cites=cites or [],
    )


def test_explicit_always_valid():
    # Observations never need citations (even if some are present).
    assert validate_cites(_rec("obs", conf="explicit", cites=["x"]), _store, set()) == "valid"


def test_inferred_empty_cites_valid():
    # Un-cited inferred is not mass-quarantined (legacy-safe).
    assert validate_cites(_rec("inf", conf="inferred", cites=[]), _store, set()) == "valid"


def test_inferred_unresolved_cite_quarantined():
    assert validate_cites(
        _rec("derived", conf="inferred", cites=["nonexistent-key-123"]), _store, set()
    ) == "quarantined"


def test_inferred_cite_in_batch_valid():
    src = _rec("source observation", conf="explicit")
    der = _rec("derived from source", conf="inferred", cites=[src.identity_key])
    assert validate_cites(der, _store, {src.identity_key}) == "valid"


def test_aaa_cites_roundtrip_and_revalidation():
    # Runs first (name sorts first): ingest a derived record whose source is
    # neither committed nor in-batch -> quarantined; verify round-trip; then
    # commit the source and revalidate -> flips to valid.
    src = _rec("the source observation text", conf="explicit")
    der = _rec("derived conclusion grounded in source", conf="inferred", cites=[src.identity_key])

    der.provenance_status = validate_cites(der, _store, set())
    assert der.provenance_status == "quarantined"
    _store.upsert(der)

    got = _store.fetch_by_id(der.identity_key)
    assert got is not None
    assert got.cites == [src.identity_key], "cites round-trips through Chroma metadata"
    assert got.provenance_status == "quarantined"
    assert project(got).provenance_status == "quarantined", "stub surfaces provenance"

    _store.upsert(src)
    flipped = revalidate_quarantined(_store, {src.identity_key})
    assert flipped >= 1
    got2 = _store.fetch_by_id(der.identity_key)
    assert got2.provenance_status == "valid", "quarantine resolves once source lands"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} provenance tests passed")


if __name__ == "__main__":
    _run_all()
