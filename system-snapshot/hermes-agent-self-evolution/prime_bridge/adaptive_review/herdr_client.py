
"""Bounded subprocess client for Herdr's local CLI/socket bridge."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import shlex
import time
from typing import Any, Callable, Mapping, Sequence


class HerdrClientError(RuntimeError):
    pass


class HerdrAmbiguousDelivery(HerdrClientError):
    pass


def _find_id(value: Any, names: tuple[str, ...]) -> str | None:
    if isinstance(value, Mapping):
        for name in names:
            found = value.get(name)
            if isinstance(found, str) and found:
                return found
        for child in value.values():
            found = _find_id(child, names)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_id(child, names)
            if found:
                return found
    return None


def _find_flag(value: Any, name: str) -> bool | None:
    if isinstance(value, Mapping):
        found = value.get(name)
        if isinstance(found, bool):
            return found
        for child in value.values():
            found = _find_flag(child, name)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_flag(child, name)
            if found is not None:
                return found
    return None


@dataclass(frozen=True)
class HerdrSessionRef:
    session: str
    workspace_id: str
    pane_id: str
    agent_id: str | None = None


class HerdrClient:
    """No-shell CLI adapter. A caller timeout after mutation is ambiguous."""

    def __init__(self, executable: Path | str, *, session: str,
                 timeout_ms: int = 30_000,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.executable = Path(executable)
        self.session = session
        self.timeout_ms = timeout_ms
        self.runner = runner
        self._server_process: subprocess.Popen | None = None

    def _argv(self, *args: str) -> list[str]:
        prefix = [str(self.executable), "--session", self.session]
        if os.name == "nt" and self.executable.suffix.lower() == ".cmd":
            prefix = ["cmd.exe", "/d", "/s", "/c", str(self.executable), "--session", self.session]
        return [*prefix, *args]

    def _call(self, *args: str, mutation: bool = False,
              timeout_ms: int | None = None,
              allow_empty_success: bool = False) -> Mapping[str, Any]:
        timeout = (timeout_ms or self.timeout_ms) / 1000
        try:
            result = self.runner(
                self._argv(*args), capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            error = HerdrAmbiguousDelivery if mutation else HerdrClientError
            raise error("Herdr command timed out") from exc
        except OSError as exc:
            raise HerdrClientError("Herdr executable unavailable") from exc
        if allow_empty_success and result.returncode == 0 and not result.stdout.strip():
            return {"result": {"accepted": True}}
        try:
            value = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            if mutation:
                raise HerdrAmbiguousDelivery("Herdr mutation returned no structured acknowledgement") from exc
            raise HerdrClientError("Herdr response is not structured JSON") from exc
        if result.returncode or not isinstance(value, Mapping) or value.get("error"):
            code = ""
            if isinstance(value, Mapping) and isinstance(value.get("error"), Mapping):
                code = str(value["error"].get("code", ""))
            if mutation and code in {"timeout", "agent_prompt_stalled", ""}:
                raise HerdrAmbiguousDelivery("Herdr mutation delivery is ambiguous")
            raise HerdrClientError("Herdr command failed")
        return value

    def _session_argv(self, *args: str) -> list[str]:
        command = [str(self.executable), "session", *args]
        if os.name == "nt" and self.executable.suffix.lower() == ".cmd":
            return ["cmd.exe", "/d", "/s", "/c", *command]
        return command

    def start_server(self, *, cwd: Path, env: Mapping[str, str]) -> None:
        """Start the isolated named server and require observable readiness."""
        environment = os.environ.copy()
        environment.update(env)
        command = self._argv("server")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._server_process = subprocess.Popen(
                command, cwd=cwd, env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise HerdrClientError("Herdr server could not start") from exc
        deadline = time.monotonic() + min(10.0, self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            if self._server_process.poll() is not None:
                raise HerdrClientError("Herdr server exited before readiness")
            try:
                status = self._call("status", "server", "--json")
                session_name = _find_id(status, ("session", "session_name", "name"))
                if _find_flag(status, "running") is True and session_name in {None, self.session}:
                    return
            except HerdrClientError:
                pass
            time.sleep(0.05)
        raise HerdrClientError("Herdr server readiness deadline exceeded")

    def close_session(self) -> None:
        """Stop and delete only this evaluator-owned named session."""
        for action in ("stop", "delete"):
            try:
                self.runner(
                    self._session_argv(action, self.session, "--json"),
                    capture_output=True, text=True, timeout=self.timeout_ms / 1000,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self._server_process is not None:
            try:
                self._server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server_process.terminate()

    def create_workspace(self, *, cwd: Path, label: str,
                         env: Mapping[str, str]) -> HerdrSessionRef:
        args = ["workspace", "create", "--cwd", str(cwd), "--label", label, "--no-focus"]
        for key, value in sorted(env.items()):
            args.extend(["--env", f"{key}={value}"])
        result = self._call(*args, mutation=True)
        workspace_id = _find_id(result, ("workspace_id",))
        pane_id = _find_id(result, ("pane_id", "terminal_id"))
        if not workspace_id or not pane_id:
            raise HerdrAmbiguousDelivery("workspace acknowledgement lacks bound identifiers")
        return HerdrSessionRef(self.session, workspace_id, pane_id)

    def run_fixed_launcher(self, pane_id: str, argv: Sequence[str]) -> Mapping[str, Any]:
        if not argv or any(not isinstance(value, str) or not value for value in argv):
            raise HerdrClientError("invalid fixed launcher argv")
        # Herdr pane run sends one command line to the pane shell. Quote the
        # already-pinned argv as one opaque command so argument boundaries cannot
        # be reinterpreted by Herdr's own space-joining convenience command.
        command = (
            subprocess.list2cmdline(list(argv))
            if os.name == "nt" else shlex.join(argv)
        )
        return self._call("pane", "run", pane_id, command, mutation=True, allow_empty_success=True)

    def wait_agent(self, target: str, *, timeout_ms: int) -> Mapping[str, Any]:
        return self._call(
            "agent", "wait", target, "--until", "idle", "--until", "done",
            "--until", "blocked", "--until", "unknown",
            "--timeout", str(timeout_ms), timeout_ms=timeout_ms + 1000,
        )

    def observe_agent(self, target: str) -> Mapping[str, Any]:
        return self._call("agent", "get", target)

    def close_workspace(self, workspace_id: str) -> None:
        self._call("workspace", "close", workspace_id, mutation=True)


__all__ = [
    "HerdrAmbiguousDelivery", "HerdrClient", "HerdrClientError", "HerdrSessionRef",
]
