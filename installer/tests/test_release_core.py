from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer.kit import (
    _payload_verification_errors,
    load_contract,
    scan_public_payload,
    scan_tree,
    selected_files,
)
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


def test_payload_excludes_generated_artifacts(tmp_path):
    source = tmp_path / "source"
    (source / "modules" / "__pycache__").mkdir(parents=True)
    (source / "modules" / "node_modules" / "package").mkdir(parents=True)
    (source / "modules" / "safe.py").write_text("safe")
    (source / "modules" / "__pycache__" / "safe.pyc").write_bytes(b"generated")
    (source / "modules" / "node_modules" / "package" / "index.js").write_text("generated")
    files = {p.as_posix() for p in selected_files(source, load_contract())}
    assert files == {"modules/safe.py"}


def test_payload_excludes_optional_provider_balance_companion(tmp_path):
    source = tmp_path / "source"
    excluded = [
        "hermes-provider-usage-extension/dashboard/provider_usage_dashboard.py",
        "hermes-provider-usage-extension/dashboard/dashboard-credentials.json",
        "hermes-provider-usage-extension/dashboard/monthly-budget-state.json",
    ]
    retained = "modules/safety/provider_call_accounting.py"
    for relative in excluded + [retained]:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    contract = load_contract()
    files = {p.as_posix() for p in selected_files(source, contract)}
    assert files == {retained}
    # Negative control: an unsafe allowlist expansion admits the companion.
    contract["groups"]["core"].append("hermes-provider-usage-extension/**")
    assert set(excluded) <= {p.as_posix() for p in selected_files(source, contract)}


def test_real_sdk_payload_has_no_provider_balance_companion():
    forbidden_names = {"provider_usage_dashboard.py", "dashboard-credentials.json", "monthly-budget-state.json"}
    files = selected_files(ROOT, load_contract())
    assert files
    assert not any("hermes-provider-usage-extension" in p.parts or p.name in forbidden_names for p in files)


@pytest.mark.parametrize("content,kind", [
    ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
    ("api_" + "key=" + "abcdefghijklmnop1234", "credential_assignment"),
    ("person" + "@" + "private.invalid", "email"),
    ("C:/" + "Users/" + "realperson/config", "home_path"),
])
def test_privacy_canaries_block(tmp_path, content, kind):
    (tmp_path / "canary.txt").write_text(content)
    assert any(hit["kind"] == kind for hit in scan_tree(tmp_path))


def test_public_payload_privacy_scope_uses_allowlist(tmp_path):
    source = tmp_path / "source"
    (source / "installer" / "contracts").mkdir(parents=True)
    (source / "modules").mkdir()
    (source / "system-snapshot").mkdir()
    contract = {
        "version": 1,
        "default_groups": ["core"],
        "groups": {"core": ["modules/**"]},
        "forbidden_roots": ["system-snapshot"],
        "forbidden_names": [".env"],
    }
    (source / "installer" / "contracts" / "public-payload.json").write_text(
        json.dumps(contract)
    )
    (source / "modules" / "safe.txt").write_text("safe")
    (source / "system-snapshot" / "private.txt").write_text(
        "api_" + "key=abcdefghijklmnop1234"
    )
    assert scan_tree(source)
    assert scan_public_payload(source) == []
    (source / "modules" / "unsafe.txt").write_text(
        "api_" + "key=abcdefghijklmnop1234"
    )
    assert scan_public_payload(source)


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
    assert result == {"cases": 6, "failures": []}


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


def test_payload_verification_does_not_generate_python_bytecode(tmp_path):
    destination = tmp_path / "public-payload"
    for relative in (
        "modules/orchestration-core/verify-core.py",
        "modules/token-compute-optimization/verify-token-compute.py",
        "modules/additive-parallel/verify-additive-parallel.py",
        "modules/conformance-kit/verify-additive-parallel.py",
        "modules/adapters/hermes/verify-hermes-adapter.py",
        "modules/adapters/verify-adapters.py",
        "installer/verify-lifecycle.py",
    ):
        script = destination / relative
        script.parent.mkdir(parents=True, exist_ok=True)
        (script.parent / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        script.write_text("import helper\n", encoding="utf-8")
    assert _payload_verification_errors(destination) == []
    generated = [
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() and (path.suffix == ".pyc" or "__pycache__" in path.parts)
    ]
    assert generated == []


def test_maintainer_policy_initializer_selftest():
    initializer = ROOT / "tools" / "init-maintainer-policy.py"
    proc = subprocess.run(
        [sys.executable, str(initializer), "--selftest"],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"cases": 3, "failures": 0}


def test_release_has_no_active_retired_document_backend():
    forbidden = (
        "light" + "rag",
        "96" + "21",
        "start-" + "light" + "rag",
        "documents" + "/scan",
        "index-" + "vault.bat",
    )
    text_suffixes = {".bat", ".cmd", ".json", ".md", ".ps1", ".py", ".sh", ".toml", ".yaml", ".yml"}
    hits = []
    ignored_parts = {".git", ".shareable-preview"}
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or ignored_parts.intersection(path.parts)
            or path.suffix.lower() not in text_suffixes
        ):
            continue
        relative = path.relative_to(ROOT)
        if relative == Path("system-snapshot/SNAPSHOT-MANIFEST.json"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            content = "\n".join(item["path"] for item in manifest["files"]).lower()
        else:
            content = path.read_text(encoding="utf-8-sig", errors="ignore").lower()
        for token in forbidden:
            if token in content:
                hits.append(f"{relative}: {token}")
    assert hits == []


def test_rag_server_has_no_machine_specific_user_fallback():
    server = (ROOT / "system" / "rag-orchestrator" / "server.py").read_text(
        encoding="utf-8-sig"
    )
    assert 'os.environ.get("USERNAME") or "' not in server
    assert 'else "python.exe"' in server
