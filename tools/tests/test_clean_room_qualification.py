from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/qualify-clean-room.py"
SPEC = importlib.util.spec_from_file_location("clean_room_qualification", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_tree_digest_is_stable_and_detects_content_drift(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    for directory in (left, right):
        (directory / "b.txt").write_text("two\n", encoding="utf-8")
        (directory / "a.txt").write_text("one\n", encoding="utf-8")
    assert MODULE.tree_digest(left) == MODULE.tree_digest(right)
    (right / "a.txt").write_text("changed\n", encoding="utf-8")
    assert MODULE.tree_digest(left) != MODULE.tree_digest(right)


def test_run_json_requires_pass_status(tmp_path):
    script = tmp_path / "hold.py"
    script.write_text('import json; print(json.dumps({"status": "HOLD"}))\n', encoding="utf-8")
    with pytest.raises(MODULE.QualificationError, match="returned HOLD"):
        MODULE.run_json([MODULE.sys.executable, str(script)], tmp_path, "fixture", (tmp_path,))


def test_local_clone_rejects_non_repository(tmp_path):
    with pytest.raises(MODULE.QualificationError, match="local non-bare Git checkout"):
        MODULE.clone_at(tmp_path, tmp_path / "clone", "0" * 40, (tmp_path,))


def test_selftest_and_static_safety_contract():
    assert MODULE.selftest() == {"cases": 2, "failures": []}
    source = SCRIPT.read_text(encoding="utf-8")
    assert "--no-hardlinks" in source
    assert "source repository must be clean" in source
    assert set(MODULE.HARNESSES) == {
        "hermes", "opencode", "pi", "antigravity", "codex", "claude-code", "moa",
    }
    assert '"provider_calls": 0' in source
    assert '"live_activation": "unchanged"' in source
    assert MODULE.inside(ROOT / "nested" / "receipt.json", ROOT)
    assert not MODULE.inside(ROOT.parent / "receipt.json", ROOT)
