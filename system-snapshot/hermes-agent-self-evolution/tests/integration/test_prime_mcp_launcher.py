from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "launch-prime-mcp.sh"


def _launch_environment(tmp_path: Path, **overrides: str) -> dict[str, str]:
    environment = {**os.environ, "HERMES_PYTHON": str(tmp_path / "fake-python"), **overrides}
    completed = subprocess.run(
        ["bash", str(LAUNCHER), "--print-effective-config"], capture_output=True, text=True,
        check=True, env=environment,
    )
    return json.loads(completed.stdout)


def test_launcher_defines_one_g_rooted_canonical_executor_store(tmp_path, monkeypatch):
    monkeypatch.delenv("LAPART_QUALIFICATION_HMAC_KEY", raising=False)
    monkeypatch.delenv("HERMES_PRIME_PROFILE_CONFIG", raising=False)
    monkeypatch.delenv("LAPART_EXECUTOR_DB", raising=False)
    monkeypatch.delenv("LAPART_GOVERNED_EXECUTOR_SRC", raising=False)
    value = _launch_environment(tmp_path)
    assert value == {
        "executor_db": "__WSL_WORKSPACE__/.state/governed-executor/tasks.sqlite3",
        "executor_src": "__WSL_WORKSPACE__/governed-executor-runtime/src",
        "interpreter": "__WSL_WORKSPACE__/hermes-agent-self-evolution/.venv/bin/python",
        "qualification_authority": "unavailable",
    }


def test_launcher_preserves_explicit_executor_overrides(tmp_path):
    value = _launch_environment(
        tmp_path,
        LAPART_EXECUTOR_DB="/tmp/existing-tasks.sqlite3",
        LAPART_GOVERNED_EXECUTOR_SRC="/tmp/existing-src",
    )
    assert value["executor_db"] == "/tmp/existing-tasks.sqlite3"
    assert value["executor_src"] == "/tmp/existing-src"
    assert value["interpreter"] == "__WSL_WORKSPACE__/hermes-agent-self-evolution/.venv/bin/python"


def test_launcher_imports_only_profile_qualification_authority(tmp_path, monkeypatch):
    monkeypatch.delenv("LAPART_QUALIFICATION_HMAC_KEY", raising=False)
    profile = tmp_path / "profiles" / "workspace"
    profile.mkdir(parents=True)
    config = profile / "config.yaml"
    config.write_text("model: {}\n", encoding="utf-8")
    (profile / ".env").write_text(
        "UNRELATED_VALUE=do-not-source\n"
        "LAPART_QUALIFICATION_HMAC_KEY=test-authority-key-123456\n",
        encoding="utf-8",
    )
    value = _launch_environment(
        tmp_path,
        HERMES_PRIME_PROFILE_CONFIG=str(config),
    )
    assert value["qualification_authority"] == "available"


def test_launcher_does_not_evaluate_profile_shell_code(tmp_path, monkeypatch):
    monkeypatch.delenv("LAPART_QUALIFICATION_HMAC_KEY", raising=False)
    sentinel = tmp_path / "must-not-exist"
    (tmp_path / ".env").write_text(
        f'UNRELATED=$(touch "{sentinel}")\n'
        f'LAPART_QUALIFICATION_HMAC_KEY=$(touch "{sentinel}")\n',
        encoding="utf-8",
    )
    _launch_environment(tmp_path, HERMES_PRIME_PROFILE_CONFIG=str(tmp_path / "config.yaml"))
    assert not sentinel.exists()
