from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
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


def test_repository_declares_cross_platform_checkout_eol_contract():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "* text=auto eol=lf" in attributes
    assert "*.ps1 text eol=crlf" in attributes
    assert "*.bat text eol=crlf" in attributes
    assert "*.cmd text eol=crlf" in attributes


def test_generated_release_manifest_uses_lf_bytes(tmp_path):
    payload = tmp_path / "payload"
    result = subprocess.run(
        [sys.executable, str(ROOT / "installer/kit.py"), "build", "--output", str(payload), "--apply", "--yes"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = (payload / "RELEASE-MANIFEST.json").read_bytes()
    assert b"\r\n" not in manifest
    assert manifest.endswith(b"}\n")


def test_bundle_uses_canonical_creator_system(tmp_path):
    bundle = tmp_path / "bundle.zip"
    result = subprocess.run(
        [sys.executable, str(ROOT / "installer/kit.py"), "bundle", "--output", str(bundle), "--apply", "--yes"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(bundle) as archive:
        assert {entry.create_system for entry in archive.infolist()} == {3}


def test_installed_controller_can_doctor_its_own_root(tmp_path):
    kit_path = ROOT / "installer/kit.py"
    spec = importlib.util.spec_from_file_location("installed_controller_fixture", kit_path)
    kit = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(kit)
    target, data = tmp_path / "installed", tmp_path / "data"
    files = kit.copy_payload(ROOT, target)
    data.mkdir()
    kit._atomic_json(target / "config.local.yaml", {"recipient": "fixture"})
    kit._atomic_json(
        target / "managed-files.json",
        kit._managed_value(target, data, files, ["config.local.yaml"], "generic"),
    )

    result = subprocess.run(
        [sys.executable, str(target / "installer/kit.py"), "doctor", "--target", str(target)],
        cwd=target, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    blocked = subprocess.run(
        [sys.executable, str(kit_path), "install", "--target", str(ROOT),
         "--data-dir", str(tmp_path / "other-data")],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert blocked.returncode == 2
    assert '"name": "source-boundary"' in blocked.stdout
