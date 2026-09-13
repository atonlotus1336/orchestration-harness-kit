from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/bootstrap-herdr.py"
SPEC = importlib.util.spec_from_file_location("herdr_bootstrap", SCRIPT)
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BOOTSTRAP)


def test_release_assets_are_closed_and_sha256_pinned():
    assert BOOTSTRAP.VERSION == "0.9.0"
    assert BOOTSTRAP.TAG == "v0.9.0"
    assert BOOTSTRAP.RELEASE_COMMIT == "b99002ac99b09e00b4ca692436cb15a6b0d676f1"
    assert set(BOOTSTRAP.ASSETS) == {
        "windows-x86_64", "linux-x86_64", "linux-aarch64",
        "macos-x86_64", "macos-aarch64",
    }
    assert all(len(item["sha256"]) == 64 for item in BOOTSTRAP.ASSETS.values())


def test_dry_run_does_not_create_installation(tmp_path):
    root = tmp_path / "herdr"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "bootstrap", "--platform", "windows-x86_64",
         "--install-root", str(root)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "DRY-RUN"
    assert payload["activation_changed"] is False
    assert not root.exists()


def test_apply_requires_explicit_yes(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "bootstrap", "--platform", "windows-x86_64",
         "--install-root", str(tmp_path / "herdr"), "--apply"],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "--apply requires --yes" in result.stderr


def test_checksum_mismatch_refuses_before_install(tmp_path):
    artifact = tmp_path / "fake.zip"
    artifact.write_bytes(b"not the pinned Herdr artifact")
    root = tmp_path / "herdr"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "bootstrap", "--platform", "windows-x86_64",
         "--install-root", str(root), "--artifact", str(artifact), "--apply", "--yes"],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "BLOCKED"
    assert not (root / ".lapart-herdr").exists()


def test_archive_path_traversal_is_rejected(tmp_path):
    archive = tmp_path / "unsafe.zip"
    import zipfile
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../herdr.exe", b"bad")
    target = tmp_path / "extract"
    target.mkdir()
    with pytest.raises(BOOTSTRAP.BootstrapError, match="unsafe path"):
        BOOTSTRAP.extract_windows(archive, target)
