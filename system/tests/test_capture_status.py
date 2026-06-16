"""
Phase-3 capture-instrumentation tests: per-harness heartbeat + harness inference.
Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="mw-cap-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.producer import write_spool, read_capture_status, _infer_harness


def test_capture_heartbeat_records_and_increments():
    s1 = Path(_TMP) / "2026-06-15-cap1.md"
    s1.write_text("## One\nfirst capture body\n", encoding="utf-8")
    write_spool(session_path=s1, project="capproj", harness="codex")

    cap = read_capture_status()
    assert "codex" in cap, cap
    assert cap["codex"]["count"] == 1
    assert cap["codex"]["last_project"] == "capproj"
    assert cap["codex"]["last_ts"]

    s2 = Path(_TMP) / "2026-06-15-cap2.md"
    s2.write_text("## Two\nsecond capture body\n", encoding="utf-8")
    write_spool(session_path=s2, project="capproj", harness="codex")
    assert read_capture_status()["codex"]["count"] == 2, "heartbeat increments per capture"

    # A different harness is tracked independently.
    s3 = Path(_TMP) / "2026-06-15-cap3.md"
    s3.write_text("## Three\nhermes capture body\n", encoding="utf-8")
    write_spool(session_path=s3, project="capproj", harness="hermes")
    cap2 = read_capture_status()
    assert set(cap2) == {"codex", "hermes"}
    assert cap2["hermes"]["count"] == 1


def test_infer_harness():
    saved = dict(os.environ)
    try:
        for k in list(os.environ):
            if k.startswith(("CLAUDE_", "CODEX_", "HERMES_")):
                del os.environ[k]
        assert _infer_harness() == "unknown"
        os.environ["CODEX_HOME"] = "/x"
        assert _infer_harness() == "codex"
        del os.environ["CODEX_HOME"]
        os.environ["HERMES_HOME"] = "/x"
        assert _infer_harness() == "hermes"
        del os.environ["HERMES_HOME"]
        os.environ["CLAUDE_SESSION_ID"] = "x"
        assert _infer_harness() == "claude-code"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} capture-status tests passed")


if __name__ == "__main__":
    _run_all()
