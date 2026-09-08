import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="POSIX bash scanner")
def test_crlf_denylist_detects_tracked_leak_and_clean_file(tmp_path):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "tools").mkdir()
    shutil.copy2(root / "tools/scan-full-repo.sh", tmp_path / "tools/scan-full-repo.sh")
    (tmp_path / ".private-denylist").write_bytes(b"synthetic-private\r\n")
    sample = tmp_path / "sample.txt"
    sample.write_text("synthetic-private must be detected")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "add", "sample.txt"], cwd=tmp_path, check=True)
    red = subprocess.run(["bash", "tools/scan-full-repo.sh"], cwd=tmp_path, capture_output=True, text=True)
    assert red.returncode == 1
    assert "LEAK" in red.stdout
    sample.write_text("public placeholder")
    green = subprocess.run(["bash", "tools/scan-full-repo.sh"], cwd=tmp_path, capture_output=True, text=True)
    assert green.returncode == 0
    assert "scan clean" in green.stdout


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="POSIX bash scanner")
@pytest.mark.parametrize("policy,content", [(b"DEVICE[-_]TEST42\r\n", "device-test42"), (b"DEVICE[-_]TEST42", "Device_Test42"), (b"[\n", "safe"), (b"# empty\n", "safe"), (None, "safe")])
def test_untracked_variants_and_invalid_policy_fail_closed(tmp_path, policy, content):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "tools").mkdir()
    shutil.copy2(root / "tools/scan-full-repo.sh", tmp_path / "tools/scan-full-repo.sh")
    if policy is not None: (tmp_path / ".private-denylist").write_bytes(policy)
    (tmp_path / "untracked.txt").write_text(content)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = subprocess.run(["bash", "tools/scan-full-repo.sh"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    assert content not in result.stdout


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="POSIX bash scanner")
def test_staged_gate_reads_crlf_policy_and_index_bytes(tmp_path):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / ".githooks").mkdir()
    shutil.copy2(root / ".githooks/pre-commit", tmp_path / ".githooks/pre-commit")
    (tmp_path / ".private-denylist").write_bytes(b"DEVICE[-_]TEST42\r\n")
    sample = tmp_path / "sample.txt"; sample.write_text("device-test42")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "add", "sample.txt"], cwd=tmp_path, check=True)
    sample.write_text("safe worktree does not clean staged bytes")
    result = subprocess.run(["bash", ".githooks/pre-commit"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1 and "LEAK" in result.stdout
    assert "device-test42" not in result.stdout
    subprocess.run(["git", "add", "sample.txt"], cwd=tmp_path, check=True)
    assert subprocess.run(["bash", ".githooks/pre-commit"], cwd=tmp_path, capture_output=True).returncode == 0


def snapshot_exporter():
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("snapshot_exporter", root / "tools/sync-private-system-snapshot.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", ["DEVICE_TEST42", "device-test42", "Device_Test42", "DEVICE[-_]TEST42", "DEVICE[_-]TEST42"])
def test_exporter_removes_case_separator_and_regex_host_variants(tmp_path, value):
    module = snapshot_exporter(); sample = tmp_path / "source.txt"; sample.write_text(value)
    assert module.sanitized_bytes(sample, [], [("DEVICE_TEST42", "stack-host")], []) == b"stack-host"


def test_exporter_honors_regex_identity_policy(tmp_path):
    module = snapshot_exporter(); sample = tmp_path / "source.txt"; sample.write_text("device-test42")
    assert module.sanitized_bytes(sample, [], [], [("DEVICE[-_]TEST42", "stack-host")]) == b"stack-host"


def test_exporter_removes_author_enrollment(tmp_path):
    import json
    module = snapshot_exporter(); sample = tmp_path / "prime-desktop-attesters.json"
    sample.write_text(json.dumps({"clients":{"stack-host":{"public_key":"synthetic-public-key","fingerprint":"synthetic-fingerprint","endpoint":"http://127.0.0.1:8642"}}}))
    client = json.loads(module.sanitized_bytes(sample, [], [], []))["clients"]["stack-host"]
    assert client["public_key"] is None and client["fingerprint"] is None
    assert client["endpoint"] == "http://127.0.0.1:8642"

@pytest.mark.parametrize("name,expected", [("script.ps1", b"one\r\ntwo\r\n"), ("script.sh", b"one\ntwo\n"), ("worker.mjs", b"one\ntwo\n"), ("local.cmd.example", b"one\ntwo\n")])
def test_snapshot_bytes_match_recipient_checkout_line_endings(tmp_path, name, expected):
    module = snapshot_exporter(); sample = tmp_path / name
    sample.write_bytes(b"one\r\ntwo\n")
    assert module.sanitized_bytes(sample, [], [], []) == expected
