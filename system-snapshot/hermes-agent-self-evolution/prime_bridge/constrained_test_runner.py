"""Hermes-owned constrained test runner for OMP worker verification."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from prime_bridge.omp_policy import OmpPolicyError


class TestRunnerError(RuntimeError):
    pass


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_COMMAND_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class TestCommand:
    command_id: str
    argv: tuple[str, ...]
    cwd: str
    environment_allowlist: tuple[str, ...]
    timeout_seconds: int
    output_cap_bytes: int
    network: str


@dataclass(frozen=True)
class TestCommandPolicy:
    commands: Mapping[str, TestCommand]
    fingerprint: str

    @classmethod
    def load(cls, path: Path) -> "TestCommandPolicy":
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TestRunnerError("test command policy is unavailable") from exc
        if not isinstance(value, dict) or value.get("schema_version") != "1.0":
            raise TestRunnerError("test command policy is invalid")
        commands: dict[str, TestCommand] = {}
        for command_id, item in value.get("commands", {}).items():
            if not _COMMAND_ID.fullmatch(command_id) or not isinstance(item, dict):
                raise TestRunnerError("test command id is invalid")
            if not isinstance(item.get("argv"), list) or not item["argv"] or any(not isinstance(a, str) for a in item["argv"]):
                raise TestRunnerError("test command argv is invalid")
            if item.get("network") not in {"none", "loopback"}:
                raise TestRunnerError("test command network rule is invalid")
            if type(item.get("timeout_seconds")) is not int or not 1 <= item["timeout_seconds"] <= 600:
                raise TestRunnerError("test command timeout is invalid")
            if type(item.get("output_cap_bytes")) is not int or not 1 <= item["output_cap_bytes"] <= 1_000_000:
                raise TestRunnerError("test command output cap is invalid")
            commands[command_id] = TestCommand(
                command_id=command_id, argv=tuple(item["argv"]),
                cwd=str(item.get("cwd", ".")),
                environment_allowlist=tuple(item.get("environment_allowlist", [])),
                timeout_seconds=item["timeout_seconds"],
                output_cap_bytes=item["output_cap_bytes"],
                network=item["network"],
            )
        return cls(commands, hashlib.sha256(raw).hexdigest())

    def command(self, command_id: str) -> TestCommand:
        try:
            return self.commands[command_id]
        except (KeyError, TypeError) as exc:
            raise TestRunnerError("test command is not in the allowlist") from exc


def run_constrained_test(
    command: TestCommand,
    *,
    project_root: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one allowlisted test command with strict bounds. Never accepts raw shell."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise TestRunnerError("project root is not a directory")
    cwd = (root / command.cwd).resolve()
    if not cwd.is_relative_to(root):
        raise TestRunnerError("test cwd escapes the project root")
    env = dict(environment or {})
    allowed = set(command.environment_allowlist)
    env = {key: value for key, value in env.items() if key in allowed}
    try:
        result = subprocess.run(
            list(command.argv), cwd=cwd, env=env, capture_output=True,
            text=True, timeout=command.timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "command_id": command.command_id, "exit_code": None}
    except OSError as exc:
        return {"status": "error", "command_id": command.command_id, "error": str(exc)}
    output = (result.stdout + result.stderr)[: command.output_cap_bytes]
    return {
        "status": "ok",
        "command_id": command.command_id,
        "argv_hash": hashlib.sha256(json.dumps(list(command.argv), sort_keys=True).encode()).hexdigest(),
        "exit_code": result.returncode,
        "output_hash": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "output_capped": len(result.stdout + result.stderr) > command.output_cap_bytes,
    }
