"""
M2 capture-quality tests: scaffolding lines never become memories, decision
entries retain source-line provenance, and failed envelopes are observable.
Runnable WITHOUT pytest.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="mw-m2-test-")
os.environ["MEMORYWEAVER_DATA_DIR"] = _TMP
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.path.join(_TMP, "fts.db")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.parser import parse_decision_line
from memoryweaver.producer import SpoolConsumer, _read_recent_decisions, write_spool


def test_scaffolding_lines_are_not_decision_memories():
    src = Path(_TMP) / "decisions-demo.md"
    for line in ("---", "# Heading", "[Append-only log - generated]", "| a | b |", "> quote"):
        assert parse_decision_line(line, src, "demo") is None, line

    rec = parse_decision_line("Keep the useful operational decision.", src, "demo")
    assert rec is not None
    assert rec.text == "Keep the useful operational decision."


def test_recent_decisions_filters_noise_and_keeps_source_line():
    src = Path(_TMP) / "decisions-demo.md"
    src.write_text(
        "\n".join(
            [
                "# Decisions",
                "[Append-only log - generated]",
                "| col | col |",
                "Keep this first decision.",
                "---",
                "> not memory",
                "Keep this second decision.",
            ]
        ),
        encoding="utf-8",
    )

    entries = _read_recent_decisions(src)
    assert [e["text"] for e in entries] == [
        "Keep this first decision.",
        "Keep this second decision.",
    ]
    assert entries[0]["source_line"] == 4
    assert entries[1]["source_line"] == 7


def test_error_envelope_is_logged_and_not_respooled():
    session = Path(_TMP) / "2026-07-05-bad.md"
    session.write_text("## Broken\nbody\n", encoding="utf-8")
    spool_path = write_spool(
        session_path=session, project="demo", harness="codex",
        spool_dir=Path(_TMP) / "spool",
    )

    class BadStore:
        def upsert(self, rec):
            raise RuntimeError("boom")

    class DummyScorer:
        def policy_version_for_record(self):
            return "test"

    consumer = SpoolConsumer(spool_dir=Path(_TMP) / "spool")
    try:
        consumer._process_one(spool_path, BadStore(), DummyScorer())
    except RuntimeError as exc:
        consumer._mark_error(spool_path, exc)

    errors = list((Path(_TMP) / "spool").glob("*.error"))
    assert len(errors) == 1
    log_path = Path(_TMP) / "logs" / "spool-errors.jsonl"
    row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert row["exception_type"] == "RuntimeError"
    assert "boom" in row["message"]

    again = write_spool(
        session_path=session, project="demo", harness="codex",
        spool_dir=Path(_TMP) / "spool",
    )
    assert again == errors[0], "same failing content is quarantined until explicit retry"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} M2 capture-quality tests passed")


if __name__ == "__main__":
    _run_all()
