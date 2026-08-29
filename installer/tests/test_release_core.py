from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer.kit import load_contract, scan_tree, selected_files
from installer.lib.redaction import redact


def run_kit(*args):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "installer" / "kit.py"), *map(str, args)],
        text=True, capture_output=True, check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_contract_rejects_traversal(tmp_path):
    contract = json.loads((ROOT / "installer/contracts/public-payload.json").read_text())
    contract["groups"]["core"].append("../private/**")
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError, match="unsafe payload pattern"):
        load_contract(path)


def test_payload_excludes_snapshot_runtime_and_symlink(tmp_path):
    source = tmp_path / "source"
    (source / "modules").mkdir(parents=True)
    (source / "modules/good.py").write_text("safe")
    (source / "system-snapshot").mkdir()
    (source / "system-snapshot/private.txt").write_text("canary")
    outside = tmp_path / "outside.txt"
    outside.write_text("escape")
    try:
        os.symlink(outside, source / "modules/escape.txt")
    except (OSError, NotImplementedError):
        pass
    files = {p.as_posix() for p in selected_files(source, load_contract())}
    assert "modules/good.py" in files
    assert not any("system-snapshot" in value or "escape.txt" in value for value in files)


@pytest.mark.parametrize("content,kind", [
    ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
    ("api_" + "key=" + "abcdefghijklmnop1234", "credential_assignment"),
    ("person" + "@" + "private.invalid", "email"),
    ("C:/" + "Users/" + "realperson/config", "home_path"),
])
def test_privacy_canaries_block(tmp_path, content, kind):
    (tmp_path / "canary.txt").write_text(content)
    assert any(hit["kind"] == kind for hit in scan_tree(tmp_path))


def test_redaction_removes_credentials_and_email():
    address = "person" + "@" + "private.invalid"
    output = redact("Authorization: Bearer abc123 " + address)
    assert "abc123" not in output and address not in output


def test_install_doctor_rerun_and_uninstall(tmp_path):
    target, data = tmp_path / "install", tmp_path / "data"
    code, dry = run_kit("install", "--target", target, "--data-dir", data)
    assert code == 0 and dry["status"] == "DRY-RUN" and not target.exists()
    for _ in range(2):
        code, result = run_kit("install", "--target", target, "--data-dir", data, "--apply", "--yes")
        assert code == 0 and result["status"] == "PASS"
        if _ == 0:
            (target / "README.md").write_text("simulated interrupted update")
    assert "simulated interrupted update" not in (target / "README.md").read_text()
    code, doctor = run_kit("doctor", "--target", target)
    assert code == 0 and doctor["status"] == "PASS"
    (data / "recipient-memory.txt").write_text("keep")
    code, removed = run_kit("uninstall", "--target", target, "--apply", "--yes")
    assert code == 0 and removed["status"] == "PASS"
    assert (data / "recipient-memory.txt").is_file()


def test_prime_omp_diagnostic_named_failure_cases():
    diagnostic = ROOT / "modules" / "prime-omp" / "diagnose-prime-omp.py"
    proc = subprocess.run(
        [sys.executable, str(diagnostic), "--selftest"],
        text=True, capture_output=True, check=False,
    )
    result = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert result == {"cases": 4, "failures": []}


def test_git_clone_prime_omp_client_path():
    verifier = ROOT / "tools" / "verify-prime-omp-client-path.py"
    proc = subprocess.run(
        [sys.executable, str(verifier), "--selftest"],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_contract_only_payload_prime_omp_gate_skips_cleanly(tmp_path):
    destination = tmp_path / "public-payload"
    code, result = run_kit("build", "--output", destination, "--apply", "--yes")
    assert code == 0 and result["status"] == "PASS"
    verifier = destination / "tools" / "verify-prime-omp-client-path.py"
    proc = subprocess.run(
        [sys.executable, str(verifier), "--selftest"],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["status"] == "SKIPPED"


def test_maintainer_policy_initializer_selftest():
    initializer = ROOT / "tools" / "init-maintainer-policy.py"
    proc = subprocess.run(
        [sys.executable, str(initializer), "--selftest"],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"cases": 3, "failures": 0}


def test_orchestration_layer_never_instructs_live_vault_lightrag_indexing():
    document = (ROOT / "docs" / "ORCHESTRATION-LAYER.md").read_text(encoding="utf-8-sig")
    assert "index-vault.bat" not in document
    assert "/documents/scan" not in document
    assert "Never point LightRAG's input directory at the vault" in document
    assert "do not use `DELETE /documents`" in document
