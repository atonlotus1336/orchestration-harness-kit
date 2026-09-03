from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
KIT_PATH = ROOT / "installer/kit.py"


def run_kit(*args):
    result = subprocess.run(
        [sys.executable, str(KIT_PATH), *map(str, args)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    return result.returncode, json.loads(result.stdout)


def install(tmp_path: Path, harness: str = "generic") -> tuple[Path, Path]:
    target, data = tmp_path / "kit", tmp_path / "data"
    code, result = run_kit(
        "install", "--target", target, "--data-dir", data,
        "--harness", harness, "--apply", "--yes",
    )
    assert code == 0 and result["status"] == "PASS"
    return target, data


def load_manifest(target: Path) -> dict:
    return json.loads((target / "managed-files.json").read_text(encoding="utf-8"))


def test_connect_is_dry_run_then_idempotent_managed_write(tmp_path):
    target, _ = install(tmp_path)
    descriptor = target / "connections/codex.json"

    code, dry = run_kit("connect", "--target", target, "--harness", "codex")
    assert code == 0 and dry["status"] == "DRY-RUN" and not descriptor.exists()

    for _ in range(2):
        code, result = run_kit(
            "connect", "--target", target, "--harness", "codex", "--apply", "--yes",
        )
        assert code == 0 and result["status"] == "PASS"
    connection = json.loads(descriptor.read_text(encoding="utf-8"))
    manifest = load_manifest(target)
    assert connection["activation"] == "installed-not-activated"
    assert connection["provider_calls"] == 0
    assert manifest["connections"] == ["codex"]
    assert manifest["files"].count("connections/codex.json") == 1


def test_cursor_connect_is_reference_only_and_provider_free(tmp_path):
    target, _ = install(tmp_path)
    code, result = run_kit(
        "connect", "--target", target, "--harness", "cursor", "--apply", "--yes",
    )
    assert code == 0 and result["status"] == "PASS"
    connection = json.loads((target / "connections/cursor.json").read_text(encoding="utf-8"))
    assert connection["activation"] == "installed-not-activated"
    assert connection["provider_calls"] == 0
    assert connection["adapter_manifest"] == "modules/adapters/cursor/harness-manifest.json"

    code, verified = run_kit("verify", "--target", target, "--harness", "cursor")
    adapter = next(check for check in verified["checks"] if check["name"] == "adapter:cursor")
    assert code == 0 and adapter["provider_calls"] == 0


def test_reinstall_preserves_local_configuration_and_connections(tmp_path):
    target, data = install(tmp_path, "codex")
    code, _ = run_kit(
        "connect", "--target", target, "--harness", "codex", "--apply", "--yes",
    )
    assert code == 0
    config = target / "config.local.yaml"
    config.write_text('{"recipient_setting": "preserve"}\n', encoding="utf-8")

    code, result = run_kit(
        "install", "--target", target, "--data-dir", data,
        "--harness", "codex", "--apply", "--yes",
    )
    assert code == 0 and result["status"] == "PASS"
    assert json.loads(config.read_text(encoding="utf-8")) == {"recipient_setting": "preserve"}
    assert (target / "connections/codex.json").is_file()
    assert load_manifest(target)["connections"] == ["codex"]


def test_connect_rejects_values_that_are_not_secret_references(tmp_path):
    target, _ = install(tmp_path)
    code, result = run_kit(
        "connect", "--target", target, "--harness", "claude-code",
        "--config-reference", "literal-credential-value", "--apply", "--yes",
    )
    assert code == 2 and result["status"] == "BLOCKED"
    assert not (target / "connections/claude-code.json").exists()


def test_verify_runs_provider_free_for_all_and_one_adapter(tmp_path):
    target, _ = install(tmp_path)
    code, all_result = run_kit("verify", "--target", target)
    assert code == 0 and all_result["status"] == "PASS"
    code, one_result = run_kit("verify", "--target", target, "--harness", "moa")
    assert code == 0 and one_result["status"] == "PASS"
    adapter = next(check for check in one_result["checks"] if check["name"] == "adapter:moa")
    assert adapter["provider_calls"] == 0


def test_upgrade_snapshot_and_rollback_restore_exact_managed_state(tmp_path):
    target, data = install(tmp_path)
    readme = target / "README.md"
    readme.write_text("previous managed release\n", encoding="utf-8")
    previous = load_manifest(target)
    previous["kit_version"] = "1.9.0-fixture"
    previous["sha256"]["README.md"] = hashlib.sha256(readme.read_bytes()).hexdigest()
    (target / "managed-files.json").write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")

    code, dry = run_kit("upgrade", "--target", target)
    assert code == 0 and dry["status"] == "DRY-RUN"
    assert not (data / "rollbacks").exists()

    code, upgraded = run_kit("upgrade", "--target", target, "--apply", "--yes")
    assert code == 0 and upgraded["status"] == "PASS"
    assert readme.read_bytes() == (ROOT / "README.md").read_bytes()
    snapshot_id = load_manifest(target)["previous_snapshot"]
    assert (data / "rollbacks" / snapshot_id / "snapshot.json").is_file()

    code, dry = run_kit("rollback", "--target", target)
    assert code == 0 and dry["status"] == "DRY-RUN"
    code, restored = run_kit("rollback", "--target", target, "--apply", "--yes")
    assert code == 0 and restored["status"] == "PASS"
    assert readme.read_text(encoding="utf-8") == "previous managed release\n"
    assert load_manifest(target)["kit_version"] == "1.9.0-fixture"


def test_snapshot_identity_includes_managed_file_content_for_v1_manifest(tmp_path):
    target, _ = install(tmp_path)
    current = load_manifest(target)
    legacy = {name: current[name] for name in ("kit_version", "install_root", "data_root", "files")}
    (target / "managed-files.json").write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("snapshot_kit", KIT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    first, _ = module._create_snapshot(target, legacy)
    (target / "README.md").write_text("different managed content\n", encoding="utf-8")
    second, _ = module._create_snapshot(target, legacy)
    assert first != second


def test_tampered_snapshot_fails_before_installed_files_change(tmp_path):
    target, data = install(tmp_path)
    before = (target / "README.md").read_bytes()
    code, result = run_kit("upgrade", "--target", target, "--apply", "--yes")
    assert code == 0 and result["status"] == "PASS"
    snapshot_id = load_manifest(target)["previous_snapshot"]
    snapshot_readme = data / "rollbacks" / snapshot_id / "files" / "README.md"
    snapshot_readme.write_text("tampered\n", encoding="utf-8")
    (target / "README.md").write_text("current state\n", encoding="utf-8")

    code, result = run_kit("rollback", "--target", target, "--apply", "--yes")
    assert code == 2 and result["status"] == "FAIL"
    assert (target / "README.md").read_text(encoding="utf-8") == "current state\n"
    assert before != (target / "README.md").read_bytes()


def test_failed_upgrade_runs_automatic_rollback(tmp_path, monkeypatch, capsys):
    target, _ = install(tmp_path)
    before_manifest = (target / "managed-files.json").read_bytes()
    before_readme = (target / "README.md").read_bytes()
    spec = importlib.util.spec_from_file_location("lifecycle_kit", KIT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    def fail_copy(*_args, **_kwargs):
        raise OSError("fixture copy failure")

    monkeypatch.setattr(module, "copy_payload", fail_copy)
    code = module.command_upgrade(SimpleNamespace(target=target, apply=True, yes=True))
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["status"] == "FAIL"
    assert output["checks"][0] == {"name": "automatic-rollback", "status": "PASS"}
    assert (target / "managed-files.json").read_bytes() == before_manifest
    assert (target / "README.md").read_bytes() == before_readme


def test_uninstall_removes_controller_but_preserves_data_and_unmanaged_files(tmp_path):
    target, data = install(tmp_path)
    (target / "recipient-note.txt").write_text("preserve", encoding="utf-8")
    (data / "recipient-memory.txt").write_text("preserve", encoding="utf-8")
    code, result = run_kit("uninstall", "--target", target, "--apply", "--yes")
    assert code == 0 and result["status"] == "PASS"
    assert not (target / "managed-files.json").exists()
    assert not (target / "config.local.yaml").exists()
    assert (target / "recipient-note.txt").read_text(encoding="utf-8") == "preserve"
    assert (data / "recipient-memory.txt").read_text(encoding="utf-8") == "preserve"


def test_v1_manifest_remains_doctor_and_connect_compatible(tmp_path):
    target, _ = install(tmp_path, "claude")
    current = load_manifest(target)
    legacy = {name: current[name] for name in ("kit_version", "install_root", "data_root", "files")}
    (target / "managed-files.json").write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
    code, doctor = run_kit("doctor", "--target", target)
    assert code == 0 and doctor["status"] == "PASS"
    code, connected = run_kit(
        "connect", "--target", target, "--harness", "claude", "--apply", "--yes",
    )
    assert code == 0 and connected["status"] == "PASS"
    assert load_manifest(target)["schema_version"] == 2


def test_lifecycle_contracts_and_current_source_verifier():
    connection = json.loads((ROOT / "installer/contracts/harness-connection.schema.json").read_text())
    rollback = json.loads((ROOT / "installer/contracts/rollback-snapshot.schema.json").read_text())
    managed = json.loads((ROOT / "installer/contracts/managed-files.schema.json").read_text())
    assert connection["properties"]["activation"]["const"] == "installed-not-activated"
    assert connection["properties"]["provider_calls"]["const"] == 0
    assert rollback["properties"]["snapshot_id"]["pattern"] == "^[0-9a-f]{16}$"
    assert managed["required"] == ["kit_version", "install_root", "data_root", "files"]
    result = subprocess.run(
        [sys.executable, str(ROOT / "installer/verify-lifecycle.py")],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_staged_payload_verification_fails_closed_on_source_drift(tmp_path):
    payload = tmp_path / "payload"
    code, result = run_kit("build", "--output", payload, "--apply", "--yes")
    assert code == 0 and result["status"] == "PASS"
    (payload / "installer/kit.py").write_text("# source drift\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("verification_kit", KIT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    failures = module._payload_verification_errors(payload)
    assert any("installer/verify-lifecycle.py" in failure for failure in failures)
