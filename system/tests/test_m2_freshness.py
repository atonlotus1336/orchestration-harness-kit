"""
M2 freshness tests: digest writes a beacon and stale reader hooks suppress body.
Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP = tempfile.mkdtemp(prefix="mw-m2-fresh-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.digest import (
    BEACON_PATH,
    DIGEST_PATH,
    is_digest_fresh,
    write_freshness_beacon,
    write_recent_digest,
)


class EmptyStore:
    def query_browse(self, confidence="all"):
        return []


def test_digest_write_updates_beacon():
    payload = write_recent_digest(store=EmptyStore(), n=3, path=DIGEST_PATH)
    assert payload["count"] == 0
    assert BEACON_PATH.exists()
    beacon = json.loads(BEACON_PATH.read_text(encoding="utf-8"))
    assert beacon["last_success"] is True
    assert beacon["digest_path"].endswith("recent-digest.json")
    assert is_digest_fresh(now=datetime.now(tz=timezone.utc)) is True


def test_stale_beacon_is_not_fresh():
    old = datetime.now(tz=timezone.utc) - timedelta(hours=48)
    write_freshness_beacon(last_digest_ts=old, last_success=True, path=BEACON_PATH)
    assert is_digest_fresh(now=datetime.now(tz=timezone.utc), max_age_seconds=7200) is False


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} M2 freshness tests passed")


if __name__ == "__main__":
    _run_all()
