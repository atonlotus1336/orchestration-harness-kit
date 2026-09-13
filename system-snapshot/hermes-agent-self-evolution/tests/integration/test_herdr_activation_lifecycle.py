import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_module():
    path = ROOT / "scripts/manage-herdr-review.py"
    spec = importlib.util.spec_from_file_location("manage_herdr_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSwitch:
    def __init__(self, path, *, fail=False):
        self.path = path
        self.fail = fail
        self.modes = {name: "off" for name in (
            "codex", "hermes", "claude-code", "moa", "cursor",
            "dsh", "pi", "opencode", "antigravity",
        )}

    def status(self, **_kwargs):
        return {"features": {"herdr-review": {"harnesses": dict(self.modes)}}}

    def set(self, _feature, mode, targets):
        if self.fail:
            raise RuntimeError("injected switch failure")
        for target in targets:
            self.modes[target] = mode
        self.path.write_text(json.dumps({"modes": self.modes}), encoding="utf-8")
        return {"changed": True}


def fixture(tmp_path, monkeypatch, *, fail=False):
    module = load_module()
    config = tmp_path / "config.json"
    lock = tmp_path / "lock.json"
    bridge = tmp_path / "bridge.py"
    catalog = tmp_path / "catalog.json"
    executable = tmp_path / "herdr.exe"
    local_app_data = tmp_path / "local"
    user_profile = tmp_path / "user"
    codex = local_app_data / "OpenAI/Codex/bin/test-build/codex.exe"
    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"codex-runtime")
    (user_profile / ".codex").mkdir(parents=True)
    bridge.write_text("bridge", encoding="utf-8")
    executable.write_bytes(b"runtime")
    config.write_text(json.dumps({
        "schema_version": "1.0", "mode": "off", "runtime_lock": str(lock),
        "targets": {"windows": {"executable": str(executable), "qualified": True}},
        "reviewers": {},
    }), encoding="utf-8")
    lock.write_text(json.dumps({
        "schema_version": "1.0", "schema_sha256": "a" * 64,
        "runnable": False, "activation_state": "inactive",
        "executables": {"windows": str(executable), "wsl": None},
    }), encoding="utf-8")
    catalog.write_text(json.dumps({
        "reviewers": [{"reviewer_id": "correctness"}, {"reviewer_id": "simplicity"}]
    }), encoding="utf-8")
    switch = FakeSwitch(tmp_path / "switch.json", fail=fail)
    monkeypatch.setattr(module, "CONFIG", config)
    monkeypatch.setattr(module, "LOCK", lock)
    monkeypatch.setattr(module, "BRIDGE", bridge)
    monkeypatch.setattr(module, "CATALOG", catalog)
    monkeypatch.setattr(module, "STATE", tmp_path / "state")
    monkeypatch.setattr(module, "SNAPSHOTS", tmp_path / "state/snapshots")
    monkeypatch.setattr(module, "RECEIPTS", tmp_path / "state/receipts")
    monkeypatch.setattr(module, "switch_owner", lambda: switch)
    monkeypatch.setattr(
        module, "probe",
        lambda *_, **__: {"status": "READY", "runnable": True},
    )
    monkeypatch.setattr(module, "evaluator_sessions", lambda *_: [])
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("USERPROFILE", str(user_profile))
    return module, switch, config, lock


def test_activate_is_atomic_and_idempotent(tmp_path, monkeypatch):
    module, switch, config, lock = fixture(tmp_path, monkeypatch)
    base_config = json.loads(config.read_text())
    base_lock = json.loads(lock.read_text())
    configured = module.configure_windows(model="gpt-5.3-codex-spark", reasoning="low")
    first = module.activate(harness="codex", model="gpt-5.3-codex-spark", reasoning="low")
    second = module.activate(harness="codex", model="gpt-5.3-codex-spark", reasoning="low")
    assert configured["status"] == "CONFIGURED"
    assert first["changed"] is True
    assert second["changed"] is False
    assert switch.modes["codex"] == "supervised"
    assert all(mode == "off" for name, mode in switch.modes.items() if name != "codex")
    runtime_config = json.loads(module.runtime_config_path().read_text())
    runtime_lock = json.loads(module.runtime_lock_path().read_text())
    assert runtime_config["mode"] == "supervised"
    assert runtime_lock["runnable"] is True
    assert json.loads(config.read_text()) == base_config
    assert json.loads(lock.read_text()) == base_lock
    command = next(iter(runtime_config["reviewers"].values()))["command"]
    assert "--codex-executable" in command
    assert command[command.index("--codex-executable") + 1].endswith("codex.exe")
    assert "--codex-home" in command


def test_activate_rolls_configuration_back_on_failure(tmp_path, monkeypatch):
    module, _switch, config, lock = fixture(tmp_path, monkeypatch, fail=True)
    module.configure_windows(model="gpt-5.3-codex-spark", reasoning="low")
    before_config = json.loads(module.runtime_config_path().read_text())
    before_lock = json.loads(module.runtime_lock_path().read_text())
    with pytest.raises(RuntimeError, match="rolled back"):
        module.activate(harness="codex", model="gpt-5.3-codex-spark", reasoning="low")
    assert json.loads(module.runtime_config_path().read_text()) == before_config
    assert json.loads(module.runtime_lock_path().read_text()) == before_lock


def test_deactivate_preserves_persistent_reviewer_configuration(tmp_path, monkeypatch):
    module, switch, config, lock = fixture(tmp_path, monkeypatch)
    module.configure_windows(model="gpt-5.3-codex-spark", reasoning="low")
    module.activate(harness="codex", model="gpt-5.3-codex-spark", reasoning="low")
    configured = json.loads(module.runtime_config_path().read_text())["reviewers"]
    result = module.deactivate()
    assert result["status"] == "OFF"
    assert json.loads(module.runtime_config_path().read_text())["reviewers"] == configured
    assert json.loads(module.runtime_config_path().read_text())["mode"] == "off"
    assert json.loads(module.runtime_lock_path().read_text())["runnable"] is False
    assert all(mode == "off" for mode in switch.modes.values())


def test_deactivate_remains_consistent_when_persistent_configuration_drifted(tmp_path, monkeypatch):
    module, switch, _config, _lock = fixture(tmp_path, monkeypatch)
    module.configure_windows(model="gpt-5.3-codex-spark", reasoning="low")
    module.activate(harness="codex", model="gpt-5.3-codex-spark", reasoning="low")
    module.BRIDGE.write_text("changed bridge", encoding="utf-8")

    result = module.deactivate()

    assert result["status"] == "OFF"
    assert result["state"]["consistent"] is True
    assert result["state"]["configuration_valid"] is False
    assert result["state"]["runtime_runnable"] is False
    assert all(mode == "off" for mode in switch.modes.values())


def test_portable_defaults_are_consistent_without_local_overlay(tmp_path, monkeypatch):
    module, switch, config, lock = fixture(tmp_path, monkeypatch)
    result = module.state()
    assert result["mode"] == "off"
    assert result["runtime_overlay_present"] is False
    assert result["configuration_valid"] is False
    assert result["consistent"] is True
    assert json.loads(config.read_text())["reviewers"] == {}
    assert json.loads(lock.read_text())["runnable"] is False
    assert all(mode == "off" for mode in switch.modes.values())


def test_activate_requires_prior_persistent_configuration(tmp_path, monkeypatch):
    module, _switch, _config, _lock = fixture(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="configure-windows first"):
        module.activate(harness="codex", model="gpt-5.3-codex-spark", reasoning="low")


def test_operation_lock_rejects_concurrent_toggle(tmp_path, monkeypatch):
    module, _switch, _config, _lock = fixture(tmp_path, monkeypatch)
    with module.OperationLock(timeout=0.1):
        with pytest.raises(RuntimeError, match="already in progress"):
            with module.OperationLock(timeout=0.1):
                pass


def test_cmd_runtime_uses_windows_command_processor(tmp_path, monkeypatch):
    module, _switch, _config, _lock = fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    command = module.herdr_command(tmp_path / "herdr.cmd", "session", "list")
    assert command[:5] == [r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c", str(tmp_path / "herdr.cmd")]


@pytest.mark.skipif(os.name == "nt", reason="WSL target test")
def test_configure_wsl_is_separate_persistent_and_inactive(tmp_path, monkeypatch):
    module, switch, config, lock = fixture(tmp_path, monkeypatch)
    herdr = tmp_path / "herdr-linux-x86_64"
    codex = tmp_path / "codex-x86_64-unknown-linux-musl"
    codex_home = tmp_path / "codex-home"
    herdr.write_bytes(b"herdr-runtime")
    codex.write_text(
        """#!/bin/sh
echo 'codex-cli 0.153.1'
""",
        encoding="utf-8",
    )
    codex.chmod(0o700)
    codex_home.mkdir()

    result = module.configure_wsl(
        model="gpt-5.3-codex-spark",
        reasoning="low",
        herdr_executable=herdr,
        codex_executable=codex,
        codex_home=codex_home,
    )

    assert result["status"] == "CONFIGURED_INACTIVE"
    assert result["state"]["consistent"] is True
    assert result["state"]["runtime_paths_verified"] is True
    runtime_config = json.loads(module.runtime_config_path("wsl").read_text())
    runtime_lock = json.loads(module.runtime_lock_path("wsl").read_text())
    assert runtime_config["mode"] == "off"
    assert runtime_config["targets"]["wsl"] == {
        "executable": str(herdr.resolve()), "qualified": True,
    }
    assert runtime_lock["activation_state"] == "inactive"
    assert runtime_lock["runnable"] is False
    assert all(mode == "off" for mode in switch.modes.values())
    assert json.loads(config.read_text())["reviewers"] == {}
    assert json.loads(lock.read_text())["executables"].get("wsl") is None
