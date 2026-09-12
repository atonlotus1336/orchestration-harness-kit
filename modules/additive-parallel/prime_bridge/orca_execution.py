"""Governed Orca execution transport backed by the existing Prime JobLedger.

Orca owns terminal/run/task/dispatch effects.  The Code Router, capability
broker, JobLedger, evidence collector, and evaluator keep their existing
authority.  In particular, an Orca ``worker_done`` message is evidence and
never changes the canonical Prime job status by itself.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import time
import uuid
from typing import Any, Mapping, Sequence

from .harness_registry import UNIVERSAL_HARNESS_REGISTRY
from .git_interop import to_local_path, to_windows_path
from .job_ledger import JobLedger, TERMINAL, validate_safe
from .orchestration_switch import effective_mode, load_mode, operation_permitted
from .worker_transport import WorkerTransport


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,511}\Z")
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "abandoned"})
MUTATING_OPERATIONS = frozenset({
    "start", "send", "stop", "abandon", "release", "retain", "retry",
    "gate-create", "gate-resolve",
})


class OrcaExecutionError(RuntimeError):
    pass


def _closed_text(value: Any, label: str, *, maximum: int = 16_384) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrcaExecutionError(f"{label} is required")
    if len(value.encode("utf-8")) > maximum:
        raise OrcaExecutionError(f"{label} exceeds its byte limit")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise OrcaExecutionError(f"invalid {label}")
    return value


def _sha256(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise OrcaExecutionError(f"invalid {label}")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise OrcaExecutionError(f"invalid {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OrcaExecutionError(f"invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise OrcaExecutionError(f"invalid {label}")
    return parsed


def _public_error(value: Mapping[str, Any] | None) -> str:
    if not isinstance(value, Mapping):
        return "ORCA_COMMAND_FAILED"
    error = value.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        if isinstance(code, str) and IDENTIFIER.fullmatch(code):
            return code
    error = value.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        if isinstance(code, str) and IDENTIFIER.fullmatch(code):
            return code
    result = value.get("result")
    if isinstance(result, Mapping):
        for key in ("status", "stage", "failedStage", "code"):
            item = result.get(key)
            if isinstance(item, str) and IDENTIFIER.fullmatch(item):
                return item
    return "ORCA_COMMAND_FAILED"


def discover_orca_executable() -> str:
    """Resolve a native or Windows-bridged Orca CLI without a machine username."""
    override = os.environ.get("LAPART_ORCA_CLI")
    if override:
        candidate = Path(override)
        if candidate.is_file() or shutil.which(override):
            return override
        raise OrcaExecutionError("configured Orca CLI is unavailable")
    native = shutil.which("orca") or shutil.which("orca.exe")
    if native:
        return native
    users = Path("/mnt/c/Users")
    candidates = sorted(
        item for item in users.glob("*/AppData/Local/Programs/orca/resources/bin/orca.exe")
        if item.is_file()
    ) if users.is_dir() else []
    if len(candidates) == 1:
        return str(candidates[0])
    if len(candidates) > 1:
        raise OrcaExecutionError("multiple Orca CLIs require LAPART_ORCA_CLI")
    return "orca"


def _windows_path_selector(value: str) -> str:
    match = re.fullmatch(r"path:/mnt/([A-Za-z])/(.*)", value)
    if match is None:
        return value
    tail = match.group(2).replace("/", "\\")
    return f"path:{match.group(1).upper()}:\\{tail}"


class OrcaCLI:
    """Strict JSON wrapper over the pinned Orca argument-vector CLI."""

    def __init__(self, executable: str | None = None, *, cwd: Path | None = None,
                 environment: str | None = None, max_output_bytes: int = 1_048_576):
        self.executable = executable or discover_orca_executable()
        self.cwd = Path(cwd).resolve() if cwd is not None else None
        self.environment = environment
        self.max_output_bytes = max_output_bytes

    def available(self) -> bool:
        return bool(shutil.which(self.executable) or Path(self.executable).is_file())

    def coordinator_command(self) -> str:
        return (
            "powershell.exe -NoLogo"
            if str(self.executable).lower().endswith(".exe") else "/bin/sh"
        )

    def call(self, arguments: Sequence[str], *, timeout: float = 30.0,
             mutating: bool = False, request_id: str | None = None,
             retry_request: bool = False) -> dict[str, Any]:
        if not arguments or any(not isinstance(item, str) or "\x00" in item for item in arguments):
            raise OrcaExecutionError("invalid Orca argument vector")
        normalized_arguments = list(arguments)
        if os.name != "nt" and str(self.executable).lower().endswith(".exe"):
            normalized_arguments = [
                _windows_path_selector(item) if item.startswith("path:")
                else to_windows_path(item) if item.startswith("/mnt/")
                else item
                for item in normalized_arguments
            ]
        command = [self.executable, *normalized_arguments]
        if self.environment:
            command.extend(["--environment", self.environment])
        if request_id is not None:
            _identifier(request_id, "request id")
        if retry_request:
            if not mutating or request_id is None:
                raise OrcaExecutionError("retry recovery requires a mutation request id")
            command.extend(["--retry-request", request_id])
        if "--json" not in command:
            command.append("--json")
        if os.name == "nt":
            # Some Orca Windows builds can mis-detect the desktop runtime when launched
            # directly by a file-backed Python process. The Node boundary
            # receives argv as base64 JSON and uses spawnSync(shell=False), so
            # no caller value is parsed as command source.
            helper = Path(__file__).resolve().parents[1] / "scripts" / "orca-spawn.mjs"
            if not helper.is_file() or not shutil.which("node"):
                raise OrcaExecutionError("safe Windows Orca launcher is unavailable")
            payload = base64.b64encode(json.dumps(command).encode("utf-8")).decode("ascii")
            command = ["node", str(helper), payload]
        attempts = 3 if not mutating else 1
        value = None
        for attempt in range(attempts):
            try:
                completed = subprocess.run(
                    command, cwd=self.cwd, capture_output=True, text=False,
                    timeout=timeout, shell=False, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.25)
                    continue
                raise OrcaExecutionError("Orca outcome is unknown after timeout") from exc
            stdout = completed.stdout or b""
            if len(stdout) > self.max_output_bytes:
                raise OrcaExecutionError("Orca response exceeds byte limit")
            try:
                value = json.loads(stdout.decode("utf-8"))
                break
            except (UnicodeError, json.JSONDecodeError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.25)
                    continue
                raise OrcaExecutionError("Orca returned invalid JSON") from exc
        if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
            raise OrcaExecutionError("Orca returned an invalid envelope")
        if completed.returncode != 0 or value["ok"] is not True:
            failure = OrcaExecutionError(_public_error(value))
            failure.envelope = value
            raise failure
        if "result" not in value:
            raise OrcaExecutionError("Orca returned an invalid envelope")
        validate_safe(value, max_bytes=self.max_output_bytes)
        return value


@dataclass(frozen=True)
class HarnessAdapter:
    harness: str
    launch: str
    agent: str | None
    command: tuple[str, ...]
    qualification: str

    @property
    def native(self) -> bool:
        return self.launch == "native"


class OrcaHarnessRegistry:
    """Owner-configured native/shim descriptors; configuration is not proof."""

    def __init__(self, adapters: Mapping[str, HarnessAdapter], *, source_sha256: str):
        self.adapters = dict(adapters)
        self.source_sha256 = source_sha256

    @classmethod
    def load(cls, path: Path | str) -> "OrcaHarnessRegistry":
        source = Path(path)
        raw_bytes = source.read_bytes()
        try:
            value = json.loads(raw_bytes)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OrcaExecutionError("Orca adapter registry is invalid") from exc
        if (not isinstance(value, dict)
                or set(value) != {"schema_version", "adapters"}
                or value.get("schema_version") != "1.0"
                or not isinstance(value.get("adapters"), dict)
                or set(value["adapters"]) != set(UNIVERSAL_HARNESS_REGISTRY)):
            raise OrcaExecutionError("Orca adapter registry is invalid")
        adapters = {}
        for harness in UNIVERSAL_HARNESS_REGISTRY:
            item = value["adapters"][harness]
            if not isinstance(item, dict) or set(item) != {
                "launch", "agent", "command", "qualification"
            }:
                raise OrcaExecutionError("Orca adapter registry is invalid")
            launch = item["launch"]
            agent = item["agent"]
            command = item["command"]
            qualification = item["qualification"]
            if launch not in {"native", "terminal"}:
                raise OrcaExecutionError("Orca adapter launch mode is invalid")
            if qualification not in {"configured", "canary", "production"}:
                raise OrcaExecutionError("Orca adapter qualification is invalid")
            if launch == "native":
                _identifier(agent, "Orca agent")
                if command != []:
                    raise OrcaExecutionError("native Orca adapter cannot carry a command")
            else:
                if agent is not None or not isinstance(command, list) or not command:
                    raise OrcaExecutionError("terminal Orca adapter requires a command")
                if any(not isinstance(part, str) or not part or "\x00" in part
                       for part in command):
                    raise OrcaExecutionError("terminal Orca command is invalid")
            adapters[harness] = HarnessAdapter(
                harness, launch, agent, tuple(command), qualification,
            )
        return cls(adapters, source_sha256=hashlib.sha256(raw_bytes).hexdigest())

    def get(self, harness: str) -> HarnessAdapter:
        try:
            return self.adapters[harness]
        except KeyError as exc:
            raise OrcaExecutionError("unsupported Orca harness") from exc

    def status(self) -> dict[str, dict[str, Any]]:
        rows = {}
        for harness in UNIVERSAL_HARNESS_REGISTRY:
            adapter = self.adapters[harness]
            executable = adapter.agent if adapter.native else adapter.command[0]
            rows[harness] = {
                "launch": adapter.launch,
                "native": adapter.native,
                "configured_qualification": adapter.qualification,
                "executable_available": bool(shutil.which(str(executable))),
            }
        return rows


def _result_id(envelope: Mapping[str, Any], entity: str) -> str:
    result = envelope.get("result")
    candidates: list[Any] = []
    if isinstance(result, Mapping):
        nested = result.get(entity)
        if isinstance(nested, Mapping):
            candidates.extend([nested.get("id"), nested.get("handle")])
        candidates.extend([
            result.get(f"{entity}Id"), result.get(f"{entity}_id"),
            result.get("handle"), result.get("id"),
        ])
    for value in candidates:
        if isinstance(value, str) and IDENTIFIER.fullmatch(value):
            return value
    raise OrcaExecutionError(f"Orca response omitted {entity} identity")


def _direct_codex_executable(fallback: str) -> str:
    override = os.environ.get("LAPART_CODEX_CLI")
    if override and Path(override).is_file():
        return override
    candidates: list[Path] = []
    if os.name == "nt":
        native = shutil.which("codex.exe")
        if native:
            candidates.append(Path(native))
    else:
        users = Path("/mnt/c/Users")
        if users.is_dir():
            candidates.extend(
                item for item in users.glob(
                    "*/AppData/Local/OpenAI/Codex/bin/*/codex.exe"
                ) if item.is_file()
            )
    unique = sorted({item.resolve() for item in candidates})
    return str(unique[0]) if len(unique) == 1 else fallback


def _terminal_adapter_command(adapter: HarnessAdapter, *, harness: str,
                              model: str | None) -> str:
    parts = list(adapter.command)
    if harness == "codex" and parts:
        parts[0] = _direct_codex_executable(parts[0])
        parts.append("--approve-for-me")
        if model:
            parts.extend(["-m", model])
    return _command_text(parts)

def _command_text(parts: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(parts))
    return shlex.join(parts)


def _wait_terminal_agent_ready(client: OrcaCLI, handle: str, *,
                               harness: str, model: str | None,
                               timeout_seconds: int = 120) -> None:
    """Wait until the startup command has produced a usable agent TUI."""
    deadline = time.monotonic() + timeout_seconds
    last = ""
    while time.monotonic() < deadline:
        envelope = client.call([
            "terminal", "read", "--terminal", handle, "--screen",
        ])
        terminal = envelope.get("result", {}).get("terminal", {})
        if terminal.get("status") in {"exited", "closed"}:
            raise OrcaExecutionError("terminal agent exited before readiness")
        rendered = terminal.get("screen") or terminal.get("tail") or ""
        if isinstance(rendered, list):
            rendered = "\n".join(str(item) for item in rendered)
        last = str(rendered)
        if harness == "codex":
            expected = str(model or "")
            if ((not expected or expected in last) and "model:" in last
                    and "loading" not in last and "Ask Codex to do anything" in last
                    and "Do you trust the contents" not in last):
                break
        elif last.strip():
            break
        time.sleep(0.5)
    else:
        raise OrcaExecutionError("terminal agent readiness timed out")
    client.call([
        "terminal", "wait", "--terminal", handle,
        "--for", "tui-idle", "--timeout-ms", "120000",
    ], timeout=130)


class OrcaExecutionProvider:
    """Execute Orca lifecycle effects while preserving Prime authority."""

    provider_name = "orca"

    def __init__(self, *, ledger: JobLedger, transport: WorkerTransport,
                 registry: OrcaHarnessRegistry, client: OrcaCLI,
                 switch_path: Path | str | None = None):
        self.ledger = ledger
        self.transport = transport
        self.registry = registry
        self.client = client
        self.switch_path = switch_path
        with ledger._connect() as db:
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='orca_executions'"
            ).fetchone()
            if schema is not None and "worker_bindings_legacy" in str(schema[0]):
                db.executescript("""
                    PRAGMA foreign_keys=OFF;
                    BEGIN EXCLUSIVE;
                    DROP INDEX IF EXISTS orca_dispatch_identity;
                    ALTER TABLE orca_executions RENAME TO orca_executions_legacy;
                    CREATE TABLE orca_executions (
                        binding_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        harness TEXT NOT NULL,
                        lifecycle TEXT NOT NULL,
                        record_json TEXT NOT NULL,
                        FOREIGN KEY(binding_id) REFERENCES worker_bindings(binding_id),
                        FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                    );
                    INSERT INTO orca_executions SELECT * FROM orca_executions_legacy;
                    DROP TABLE orca_executions_legacy;
                    COMMIT;
                    PRAGMA foreign_keys=ON;
                """)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS orca_executions (
                    binding_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    harness TEXT NOT NULL,
                    lifecycle TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(binding_id) REFERENCES worker_bindings(binding_id),
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS orca_dispatch_identity
                    ON orca_executions(json_extract(record_json,'$.dispatch_id'))
                    WHERE json_extract(record_json,'$.dispatch_id') IS NOT NULL;
            """)

    def _permit(self, harness: str, operation: str) -> None:
        if not operation_permitted("orca", harness, operation, self.switch_path):
            raise OrcaExecutionError(f"Orca {operation} is disabled for {harness}")

    def allocate_worktree(self, repo: Path, root: Path, binding_id: str,
                          base_commit: str) -> Path:
        """Create or reconcile one Orca-owned checkout under Prime's root."""
        _identifier(binding_id, "binding id")
        if not re.fullmatch(r"[a-f0-9]{40,64}", base_commit):
            raise OrcaExecutionError("invalid Orca worktree base commit")

        def repo_inventory() -> list[Mapping[str, Any]]:
            rows = self.client.call(["repo", "list"]).get("result", {}).get("repos")
            if not isinstance(rows, list):
                raise OrcaExecutionError("Orca repo inventory is incomplete")
            return [item for item in rows if isinstance(item, Mapping)]

        try:
            registered = self.client.call([
                "repo", "add", "--path", str(repo),
            ], mutating=True)
            repo_record = registered.get("result", {}).get("repo")
        except OrcaExecutionError as exc:
            if str(exc) != "Orca returned invalid JSON":
                raise
            matches = []
            for attempt in range(20):
                matches = [
                    item for item in repo_inventory()
                    if isinstance(item.get("path"), str)
                    and to_local_path(item["path"]).resolve() == repo.resolve()
                ]
                if len(matches) == 1:
                    break
                if len(matches) > 1:
                    raise OrcaExecutionError("ambiguous Orca repo registration") from exc
                time.sleep(0.25)
            if len(matches) != 1:
                raise OrcaExecutionError("Orca repo registration remained unobserved") from exc
            repo_record = matches[0]
        if (not isinstance(repo_record, Mapping)
                or not isinstance(repo_record.get("id"), str)):
            raise OrcaExecutionError("Orca repo registration is incomplete")
        repo_id = _identifier(repo_record["id"], "Orca repo id")
        try:
            self.client.call([
                "project", "setup-update", "--setup", repo_id,
                "--worktree-base-path", str(root),
            ], mutating=True)
        except OrcaExecutionError as exc:
            if str(exc) != "Orca returned invalid JSON":
                raise
            configured = [
                item for item in repo_inventory() if item.get("id") == repo_id
                and isinstance(item.get("worktreeBasePath"), str)
                and to_local_path(item["worktreeBasePath"]).resolve() == root.resolve()
            ]
            if len(configured) != 1:
                raise OrcaExecutionError("ambiguous Orca setup update") from exc

        def worktree_matches() -> list[Mapping[str, Any]]:
            rows = self.client.call([
                "worktree", "list", "--repo", "id:" + repo_id,
            ]).get("result", {}).get("worktrees")
            if not isinstance(rows, list):
                raise OrcaExecutionError("Orca worktree inventory is incomplete")
            return [
                item for item in rows if isinstance(item, Mapping)
                and item.get("repoId") == repo_id
                and item.get("displayName") == binding_id
            ]

        matches = worktree_matches()
        if len(matches) > 1:
            raise OrcaExecutionError("Orca worktree identity is ambiguous")
        if matches:
            worktree = matches[0]
        else:
            try:
                created = self.client.call([
                    "worktree", "create", "--name", binding_id,
                    "--repo", "id:" + repo_id, "--setup", "skip",
                    "--base-branch", base_commit, "--no-parent",
                ], timeout=120, mutating=True)
                worktree = created.get("result", {}).get("worktree")
            except OrcaExecutionError as exc:
                if str(exc) != "Orca returned invalid JSON":
                    raise
                matches = worktree_matches()
                if len(matches) != 1:
                    raise OrcaExecutionError("ambiguous Orca worktree creation") from exc
                worktree = matches[0]
            if not isinstance(worktree, Mapping):
                raise OrcaExecutionError("Orca worktree creation is incomplete")
        if (worktree.get("repoId") != repo_id
                or worktree.get("displayName") != binding_id
                or worktree.get("head") != base_commit
                or not isinstance(worktree.get("path"), str)):
            raise OrcaExecutionError("Orca worktree identity mismatch")
        local = to_local_path(worktree["path"]).resolve(strict=True)
        if (local.name != binding_id or not local.is_relative_to(root.resolve())):
            raise OrcaExecutionError("Orca worktree escaped the Prime root")
        return local

    def active_workers(self, harnesses=None) -> list[dict[str, Any]]:
        selected = tuple(harnesses or UNIVERSAL_HARNESS_REGISTRY)
        if not selected or set(selected) - set(UNIVERSAL_HARNESS_REGISTRY):
            raise OrcaExecutionError("unsupported Orca harness")
        placeholders = ",".join("?" for _ in selected)
        with self.ledger._connect() as db:
            rows = db.execute(
                f"SELECT record_json FROM orca_executions WHERE harness IN ({placeholders})",
                selected,
            ).fetchall()
        active = []
        for row in rows:
            record = json.loads(row[0])
            validate_safe(record)
            if record.get("lifecycle") not in TERMINAL_STATES:
                active.append({
                    "binding_id": record["binding_id"],
                    "job_id": record["job_id"],
                    "harness": record["harness"],
                    "dispatch_id": record.get("dispatch_id"),
                    "lifecycle": record.get("lifecycle"),
                    "stage": record.get("stage"),
                })
        return sorted(active, key=lambda item: (item["harness"], item["binding_id"]))

    def status(self, harnesses=None) -> dict[str, Any]:
        selected = tuple(harnesses or UNIVERSAL_HARNESS_REGISTRY)
        active = self.active_workers(selected)
        cli_ready = self.client.available()
        rows = {}
        for harness in selected:
            adapter = self.registry.get(harness)
            executable = adapter.agent if adapter.native else adapter.command[0]
            executable_ready = bool(shutil.which(str(executable)))
            qualified = adapter.qualification in {"canary", "production"}
            rows[harness] = {
                "desired_mode": load_mode("orca", harness, self.switch_path),
                "effective_mode": effective_mode("orca", harness, self.switch_path),
                "runtime_ready": (
                    "READY" if cli_ready and executable_ready and qualified
                    else "UNQUALIFIED" if cli_ready and executable_ready
                    else "BLOCKED"
                ),
                "qualification": adapter.qualification,
                "launch": adapter.launch,
                "active_workers": sum(1 for item in active if item["harness"] == harness),
            }
        return {
            "provider": self.provider_name,
            "runtime_ready": (
                "READY" if rows and all(row["runtime_ready"] == "READY" for row in rows.values())
                else "PARTIAL" if any(row["runtime_ready"] != "BLOCKED" for row in rows.values())
                else "BLOCKED"
            ),
            "active_workers": len(active),
            "harnesses": rows,
        }

    def drain(self, harnesses) -> dict[str, Any]:
        selected = tuple(harnesses)
        before = self.active_workers(selected)
        stopped = []
        failures = []
        for item in before:
            if not item.get("dispatch_id"):
                failures.append({"binding_id": item["binding_id"], "reason": "DISPATCH_UNKNOWN"})
                continue
            try:
                self.stop(item["binding_id"])
                stopped.append(item["binding_id"])
            except Exception as exc:
                failures.append({"binding_id": item["binding_id"], "reason": type(exc).__name__})
        remaining = self.active_workers(selected)
        return {
            "status": "DRAINED" if not remaining else "INCOMPLETE",
            "attempted": len(before), "stopped": len(stopped),
            "remaining": len(remaining), "failures": failures,
        }

    def _load(self, binding_id: str) -> dict[str, Any]:
        with self.ledger._connect() as db:
            row = db.execute(
                "SELECT record_json FROM orca_executions WHERE binding_id=?",
                (binding_id,),
            ).fetchone()
        if row is None:
            raise OrcaExecutionError("unknown Orca execution")
        value = json.loads(row[0])
        validate_safe(value)
        return value

    def _store(self, record: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(record)
        validate_safe(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self.ledger._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO orca_executions(binding_id,job_id,harness,lifecycle,record_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(binding_id) DO UPDATE SET "
                "lifecycle=excluded.lifecycle,record_json=excluded.record_json",
                (value["binding_id"], value["job_id"], value["harness"],
                 value["lifecycle"], encoded),
            )
            db.commit()
        return value

    def _note(self, record: Mapping[str, Any], kind: str,
              data: Mapping[str, Any]) -> None:
        self.ledger.note(record["job_id"], kind, {
            "binding_id": record["binding_id"],
            "dispatch_id": record.get("dispatch_id"),
            **dict(data),
        })

    def _authority(self, receipt: Mapping[str, Any], harness: str,
                   context: Mapping[str, str]) -> None:
        required = {
            "job_id", "profile", "harness", "request_id", "capability_receipt"
        }
        if not isinstance(context, Mapping) or set(context) != required:
            raise OrcaExecutionError("closed Orca capability context is required")
        if (context["job_id"] != receipt["job_id"]
                or context["harness"] != harness):
            raise OrcaExecutionError("Orca capability identity mismatch")
        capability_digest = hashlib.sha256(
            context["capability_receipt"].encode("utf-8")
        ).hexdigest()
        matching = [
            event for event in self.ledger.events(receipt["job_id"])
            if event.get("kind") == "capability_authorized"
            and event.get("data", {}).get("request_id") == context["request_id"]
            and event.get("data", {}).get("alias") == "orca.worker.start"
            and event.get("data", {}).get("capability_digest") == capability_digest
        ]
        if not matching:
            raise OrcaExecutionError("Orca capability authority is unavailable")

    def probe(self, harness: str) -> dict[str, Any]:
        self._permit(harness, "probe")
        adapter = self.registry.get(harness)
        if not self.client.available():
            return {
                "status": "BLOCKED", "reason": "ORCA_CLI_MISSING",
                "harness": harness, "adapter": adapter.launch,
            }
        ready = False
        runtime: Mapping[str, Any] = {}
        samples: list[dict[str, Any]] = []
        try:
            # Orca can return one stale bootstrap sample while reconnecting. A
            # bounded immediate retry observes state; it never launches Orca.
            for _attempt in range(3):
                envelope = self.client.call(["status"], timeout=15)
                result = envelope.get("result", {})
                runtime = (
                    result.get("runtime", {})
                    if isinstance(result, Mapping) else {}
                )
                ready = bool(runtime.get("reachable"))
                samples.append({"state": runtime.get("state"), "reachable": ready})
                if ready:
                    break
                time.sleep(0.25)
        except OrcaExecutionError:
            return {
                "status": "BLOCKED", "reason": "ORCA_RUNTIME_UNAVAILABLE",
                "harness": harness, "adapter": adapter.launch,
            }
        executable = adapter.agent if adapter.native else adapter.command[0]
        executable_ready = bool(shutil.which(str(executable)))
        return {
            "status": "READY" if ready and executable_ready else "BLOCKED",
            "reason": (
                "READY" if ready and executable_ready
                else "HARNESS_EXECUTABLE_MISSING" if ready
                else "ORCA_RUNTIME_UNAVAILABLE"
            ),
            "harness": harness, "adapter": adapter.launch,
            "native": adapter.native,
            "configured_qualification": adapter.qualification,
            "runtime_reachable": ready,
            "runtime_state": runtime.get("state"),
            "samples": samples,
            "connection_state": runtime.get("connectionState"),
            "executable_available": executable_ready,
            "registry_sha256": self.registry.source_sha256,
        }

    def start(self, *, binding_id: str, objective: str, task_spec: str,
              context: Mapping[str, str], model: str | None = None,
              effort: str | None = None, environment: str | None = None) -> dict[str, Any]:
        receipt, _row = self.transport._binding(binding_id)
        harness = receipt["harness"]
        self._permit(harness, "start")
        if environment is not None:
            self._permit(harness, "remote")
            _identifier(environment, "Orca environment")
        self._authority(receipt, harness, context)
        if self.ledger.get(receipt["job_id"])["status"] in TERMINAL:
            raise OrcaExecutionError("terminal Prime job cannot launch Orca")
        if self.probe(harness).get("status") != "READY":
            raise OrcaExecutionError("Orca execution target is not READY")
        adapter = self.registry.get(harness)
        objective = _closed_text(objective, "objective", maximum=8_192)
        task_spec = _closed_text(task_spec, "task spec")
        if model is not None:
            _identifier(model, "model")
        if effort not in {None, "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise OrcaExecutionError("invalid reasoning effort")
        if effort is not None and model is None:
            raise OrcaExecutionError("Orca effort requires an explicit model")
        try:
            existing = self._load(binding_id)
        except OrcaExecutionError:
            existing = None
        if existing is not None:
            if existing["lifecycle"] in TERMINAL_STATES:
                raise OrcaExecutionError("terminal Orca execution requires explicit retry")
            return existing
        record = {
            "schema_version": "1.0", "binding_id": binding_id,
            "job_id": receipt["job_id"], "harness": harness,
            "adapter": adapter.launch, "native": adapter.native,
            "registry_sha256": self.registry.source_sha256,
            "run_id": None, "task_id": None, "dispatch_id": None,
            "terminal_handle": None, "coordinator_terminal_handle": None,
            "environment": environment,
            "lifecycle": "starting", "stage": "claimed",
            "read_cursor": None, "last_delivery_id": None,
            "objective_sha256": hashlib.sha256(objective.encode()).hexdigest(),
            "task_spec_sha256": hashlib.sha256(task_spec.encode()).hexdigest(),
            "capability_request_id": context["request_id"],
        }
        self._store(record)
        self._note(record, "orca_execution_claimed", {
            "adapter": adapter.launch,
            "registry_sha256": self.registry.source_sha256,
        })
        try:
            coordinator = self.client.call([
                "terminal", "create", "--worktree", "path:" + receipt["worktree"],
                "--command", self.client.coordinator_command(),
                "--title", f"orca-{harness}-coordinator",
            ], mutating=True)
            record["coordinator_terminal_handle"] = _result_id(coordinator, "terminal")
            record["stage"] = "coordinator-created"
            self._store(record)
            run_request = uuid.uuid4().hex
            envelope = self.client.call(
                ["orchestration", "run-create", "--objective", objective,
                 "--from", record["coordinator_terminal_handle"]],
                mutating=True, request_id=run_request,
            )
            record.update(run_id=_result_id(envelope, "run"), stage="run-created")
            self._store(record)
            task_request = uuid.uuid4().hex
            envelope = self.client.call([
                "orchestration", "task-create", "--spec", task_spec,
                "--run", record["run_id"],
                "--from", record["coordinator_terminal_handle"],
            ], mutating=True, request_id=task_request)
            record.update(task_id=_result_id(envelope, "task"), stage="task-created")
            self._store(record)
            worker_args = [
                "orchestration", "worker-start", "--task", record["task_id"],
                "--run", record["run_id"], "--worktree",
                "path:" + receipt["worktree"],
                "--from", record["coordinator_terminal_handle"],
                "--timeout-ms", "180000",
            ]
            if environment is not None:
                worker_args.extend(["--on", environment])
            if adapter.native:
                worker_args.extend(["--agent", str(adapter.agent)])
                if model is not None:
                    worker_args.extend(["--model", model])
                if effort is not None:
                    worker_args.extend(["--effort", effort])
            else:
                terminal = self.client.call([
                    "terminal", "create", "--worktree",
                    "path:" + receipt["worktree"], "--command",
                    _terminal_adapter_command(adapter, harness=harness, model=model), "--title", f"orca-{harness}",
                ], mutating=True)
                record["terminal_handle"] = _result_id(terminal, "terminal")
                record["stage"] = "terminal-created"
                self._store(record)
                _wait_terminal_agent_ready(
                    self.client, record["terminal_handle"],
                    harness=harness, model=model,
                )
                record["stage"] = "terminal-ready"
                self._store(record)
                worker_args.extend(["--terminal", record["terminal_handle"]])
            worker_request = uuid.uuid4().hex
            record["worker_request_id"] = worker_request
            self._store(record)
            try:
                envelope = self.client.call(
                    worker_args, timeout=210, mutating=True,
                    request_id=worker_request,
                )
            except OrcaExecutionError as exc:
                outcome_request_id = getattr(exc, "envelope", {}).get("id")
                try:
                    uuid.UUID(str(outcome_request_id))
                except (ValueError, TypeError, AttributeError):
                    outcome_request_id = None
                if (str(exc) != "turn_start_unobserved"
                        or outcome_request_id is None):
                    raise
                record["worker_outcome_request_id"] = outcome_request_id
                self._store(record)
                inventory = self.client.call([
                    "orchestration", "worker-list", "--run", record["run_id"],
                ])
                workers = inventory.get("result", {}).get("workers")
                matches = [
                    item for item in workers if isinstance(item, Mapping)
                    and item.get("runId") == record["run_id"]
                    and item.get("taskId") == record["task_id"]
                    and item.get("dispatchStatus") in {
                        "pending", "dispatched", "completed", "failed",
                    }
                    and isinstance(item.get("dispatchId"), str)
                ] if isinstance(workers, list) else []
                if len(matches) != 1:
                    raise OrcaExecutionError(
                        "unobserved Orca worker could not be reconciled"
                    ) from exc
                matched = matches[0]
                agent_terminal = matched.get("agentTerminalHandle")
                if isinstance(agent_terminal, str) and agent_terminal:
                    _wait_terminal_agent_ready(
                        self.client, agent_terminal,
                        harness=harness, model=model,
                    )
                    screen = self.client.call([
                        "terminal", "read", "--terminal", agent_terminal,
                        "--screen",
                    ])
                    terminal = screen.get("result", {}).get("terminal", {})
                    draft = terminal.get("draft")
                    if isinstance(draft, str) and draft.strip():
                        self.client.call([
                            "terminal", "send", "--terminal", agent_terminal,
                            "--enter", "--wait-submit", "60",
                        ], timeout=70, mutating=True,
                           request_id=uuid.uuid4().hex)
                        record["stage"] = "worker-draft-submitted"
                envelope = {"result": {"dispatch": {
                    "id": _identifier(
                        matched["dispatchId"], "unobserved dispatch",
                    ),
                }}}
                if record.get("stage") != "worker-draft-submitted":
                    record["stage"] = "worker-start-reconciled"
            record.update(
                dispatch_id=_result_id(envelope, "dispatch"),
                lifecycle="ready", stage="worker-ready",
            )
            self._store(record)
            self.transport.bind_orca(binding_id, record["dispatch_id"])
            self._note(record, "orca_execution_ready", {
                "run_id": record["run_id"], "task_id": record["task_id"],
                "adapter": adapter.launch,
            })
            return record
        except BaseException as exc:
            record.update(lifecycle="outcome_unknown", error=type(exc).__name__)
            self._store(record)
            self._note(record, "orca_execution_uncertain", {"stage": record["stage"]})
            raise

    def show(self, binding_id: str) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "show")
        if not record.get("dispatch_id"):
            return record
        envelope = self.client.call([
            "orchestration", "worker-show", "--dispatch", record["dispatch_id"],
        ])
        result = envelope["result"]
        digest = hashlib.sha256(json.dumps(
            result, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        self._note(record, "orca_execution_observed", {"result_sha256": digest})
        return {
            "execution": record, "worker": result,
            "worker_envelope": envelope, "result_sha256": digest,
        }

    def read(self, binding_id: str, *, limit: int = 50,
             source: str = "auto") -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "read")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise OrcaExecutionError("invalid Orca read limit")
        if source not in {"auto", "transcript", "terminal"}:
            raise OrcaExecutionError("invalid Orca read source")
        arguments = [
            "orchestration", "worker-read", "--dispatch", record["dispatch_id"],
            "--source", source, "--limit", str(limit),
        ]
        if record.get("read_cursor"):
            arguments.extend(["--cursor", record["read_cursor"]])
        envelope = self.client.call(arguments)
        result = envelope["result"]
        cursor = result.get("cursor") if isinstance(result, Mapping) else None
        if cursor is not None:
            record["read_cursor"] = _identifier(cursor, "Orca read cursor")
            self._store(record)
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        self._note(record, "orca_output_read", {
            "result_sha256": hashlib.sha256(encoded).hexdigest(),
            "result_bytes": len(encoded), "source": source,
        })
        return {"execution": record, "result": result}

    def send(self, binding_id: str, *, subject: str, body: str) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "send")
        subject = _closed_text(subject, "message subject", maximum=512)
        body = _closed_text(body, "message body", maximum=8_192)
        envelope = self.client.call([
            "orchestration", "send", "--to", "dispatch:" + record["dispatch_id"],
            "--subject", subject, "--body", body, "--type", "status",
            "--run", record["run_id"],
        ], mutating=True, request_id=uuid.uuid4().hex)
        self._note(record, "orca_message_sent", {
            "subject_sha256": hashlib.sha256(subject.encode()).hexdigest(),
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        })
        return envelope["result"]

    def check(self, binding_id: str, *, wait: bool = False,
              timeout_ms: int = 30_000, acknowledge: bool = False) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "check")
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 60_000:
            raise OrcaExecutionError("invalid Orca check timeout")
        arguments = [
            "orchestration", "check", "--run", record["run_id"],
            "--types", "worker_done,escalation,question",
        ]
        if acknowledge:
            if not record.get("last_delivery_id"):
                raise OrcaExecutionError("no Orca delivery is available to acknowledge")
            arguments.extend(["--ack", record["last_delivery_id"]])
        if wait:
            arguments.extend(["--wait", "--timeout-ms", str(timeout_ms)])
        envelope = self.client.call(arguments, timeout=(timeout_ms / 1000) + 10)
        result = envelope["result"]
        delivery = result.get("deliveryId") if isinstance(result, Mapping) else None
        if delivery is not None:
            record["last_delivery_id"] = _identifier(delivery, "delivery id")
            self._store(record)
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        self._note(record, "orca_inbox_checked", {
            "result_sha256": hashlib.sha256(encoded).hexdigest(),
            "waited": wait, "acknowledged": acknowledge,
            "canonical_status_changed": False,
        })
        return {"execution": record, "result": result,
                "canonical_status_changed": False}

    def _worker_mutation(self, binding_id: str, operation: str) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], operation)
        envelope = self.client.call([
            "orchestration", "worker-" + operation,
            "--dispatch", record["dispatch_id"],
        ], timeout=60, mutating=True, request_id=uuid.uuid4().hex)
        state = {
            "stop": "cancelled", "abandon": "abandoned",
            "release": "completed", "retain": record["lifecycle"],
        }[operation]
        record["lifecycle"] = state
        record["stage"] = "worker-" + operation
        self._store(record)
        self._note(record, "orca_worker_" + operation, {
            "canonical_status_changed": False,
        })
        if operation == "release" and record.get("coordinator_terminal_handle"):
            self.client.call([
                "terminal", "close", "--terminal",
                record["coordinator_terminal_handle"],
            ], mutating=True)
            record["coordinator_terminal_handle"] = None
            self._store(record)
            self._note(record, "orca_coordinator_released", {
                "canonical_status_changed": False,
            })
        return {"execution": record, "result": envelope["result"],
                "canonical_status_changed": False}

    def stop(self, binding_id: str) -> dict[str, Any]:
        return self._worker_mutation(binding_id, "stop")

    def abandon(self, binding_id: str) -> dict[str, Any]:
        return self._worker_mutation(binding_id, "abandon")

    def release(self, binding_id: str) -> dict[str, Any]:
        return self._worker_mutation(binding_id, "release")

    def retain(self, binding_id: str) -> dict[str, Any]:
        return self._worker_mutation(binding_id, "retain")

    def retry(self, binding_id: str, *, context: Mapping[str, str],
              model: str | None = None, effort: str | None = None,
              environment: str | None = None) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "retry")
        receipt, _row = self.transport._binding(binding_id)
        self._authority(receipt, record["harness"], context)
        if record["lifecycle"] not in TERMINAL_STATES | {"outcome_unknown"}:
            raise OrcaExecutionError("only a terminal or uncertain Orca attempt can retry")
        if effort is not None and model is None:
            raise OrcaExecutionError("Orca effort requires an explicit model")
        adapter = self.registry.get(record["harness"])
        prior_dispatch = _identifier(record["dispatch_id"], "prior dispatch")
        arguments = [
            "orchestration", "worker-start", "--task", record["task_id"],
            "--run", record["run_id"], "--retry-of", prior_dispatch,
            "--worktree", "path:" + receipt["worktree"],
        ]
        selected_environment = environment or record.get("environment")
        if selected_environment is not None:
            self._permit(record["harness"], "remote")
            arguments.extend(["--on", _identifier(selected_environment, "Orca environment")])
        if adapter.native:
            arguments.extend(["--agent", str(adapter.agent)])
            if model is not None:
                arguments.extend(["--model", _identifier(model, "model")])
            if effort is not None:
                arguments.extend(["--effort", effort])
        else:
            terminal = self.client.call([
                "terminal", "create", "--worktree", "path:" + receipt["worktree"],
                "--command", _terminal_adapter_command(adapter, harness=record["harness"], model=model),
                "--title", f"orca-{record['harness']}-retry",
            ], mutating=True)
            record["terminal_handle"] = _result_id(terminal, "terminal")
            _wait_terminal_agent_ready(
                self.client, record["terminal_handle"],
                harness=record["harness"], model=model,
            )
            arguments.extend(["--terminal", record["terminal_handle"]])
        envelope = self.client.call(
            arguments, timeout=60, mutating=True, request_id=uuid.uuid4().hex,
        )
        new_dispatch = _result_id(envelope, "dispatch")
        self.transport.rebind_orca(binding_id, prior_dispatch, new_dispatch)
        attempts = list(record.get("prior_dispatches", []))
        attempts.append(prior_dispatch)
        record.update(
            dispatch_id=new_dispatch, prior_dispatches=attempts,
            lifecycle="ready", stage="worker-retried",
            capability_request_id=context["request_id"],
        )
        self._store(record)
        self._note(record, "orca_worker_retried", {
            "prior_dispatch_sha256": hashlib.sha256(prior_dispatch.encode()).hexdigest(),
            "canonical_status_changed": False,
        })
        return record

    def task_create(self, binding_id: str, *, spec: str,
                    dependencies: Sequence[str] = (),
                    parent: str | None = None,
                    title: str | None = None) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "task-create")
        spec = _closed_text(spec, "task spec")
        if (not isinstance(dependencies, (list, tuple)) or len(dependencies) > 128
                or any(not isinstance(item, str) or IDENTIFIER.fullmatch(item) is None
                       for item in dependencies)):
            raise OrcaExecutionError("invalid task dependencies")
        arguments = [
            "orchestration", "task-create", "--spec", spec,
            "--run", record["run_id"],
        ]
        if dependencies:
            arguments.extend(["--deps", json.dumps(list(dependencies))])
        if parent is not None:
            arguments.extend(["--parent", _identifier(parent, "parent task")])
        if title is not None:
            arguments.extend(["--task-title", _closed_text(title, "task title", maximum=512)])
        envelope = self.client.call(
            arguments, mutating=True, request_id=uuid.uuid4().hex,
        )
        result = envelope["result"]
        self._note(record, "orca_task_created", {
            "task_id": _result_id(envelope, "task"),
            "spec_sha256": hashlib.sha256(spec.encode()).hexdigest(),
            "dependency_count": len(dependencies),
        })
        return result

    def task_list(self, binding_id: str, *, ready: bool = False,
                  brief: bool = True, status: str | None = None) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "task-list")
        arguments = ["orchestration", "task-list", "--run", record["run_id"]]
        if ready:
            arguments.append("--ready")
        if brief:
            arguments.append("--brief")
        if status is not None:
            allowed = {"pending", "ready", "dispatched", "completed", "failed", "blocked"}
            if status not in allowed:
                raise OrcaExecutionError("invalid task status")
            arguments.extend(["--status", status])
        return self.client.call(arguments)["result"]

    def task_update(self, binding_id: str, *, task_id: str, status: str,
                    result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "task-update")
        if status not in {"pending", "ready", "dispatched", "completed", "failed", "blocked"}:
            raise OrcaExecutionError("invalid task status")
        arguments = [
            "orchestration", "task-update", "--id", _identifier(task_id, "task id"),
            "--status", status, "--run", record["run_id"],
        ]
        if result is not None:
            validate_safe(result, max_bytes=16_384)
            arguments.extend(["--result", json.dumps(result, sort_keys=True, separators=(",", ":"))])
        envelope = self.client.call(
            arguments, mutating=True, request_id=uuid.uuid4().hex,
        )
        self._note(record, "orca_task_updated", {
            "task_id": task_id, "status": status,
            "canonical_status_changed": False,
        })
        return {"result": envelope["result"], "canonical_status_changed": False}

    def reply(self, binding_id: str, *, message_id: str,
              body: str) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "reply")
        body = _closed_text(body, "reply body", maximum=8_192)
        envelope = self.client.call([
            "orchestration", "reply", "--id", _identifier(message_id, "message id"),
            "--body", body, "--run", record["run_id"],
        ], mutating=True, request_id=uuid.uuid4().hex)
        self._note(record, "orca_message_replied", {
            "message_id": message_id,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        })
        return envelope["result"]

    def inbox(self, binding_id: str, *, limit: int = 50,
              full: bool = False) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "inbox")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise OrcaExecutionError("invalid inbox limit")
        arguments = ["orchestration", "inbox", "--limit", str(limit)]
        if record.get("terminal_handle"):
            arguments.extend(["--terminal", record["terminal_handle"]])
        if full:
            arguments.append("--full")
        return self.client.call(arguments)["result"]

    def worker_list(self, binding_id: str) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "show")
        return self.client.call([
            "orchestration", "worker-list", "--run", record["run_id"],
        ])["result"]

    def gate_create(self, binding_id: str, *, question: str,
                    options: Sequence[str] = ()) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "gate-create")
        question = _closed_text(question, "gate question", maximum=2_048)
        if (not isinstance(options, (list, tuple)) or len(options) > 16
                or any(not isinstance(item, str) or not item for item in options)):
            raise OrcaExecutionError("invalid gate options")
        arguments = [
            "orchestration", "gate-create", "--task", record["task_id"],
            "--question", question,
        ]
        if options:
            arguments.extend(["--options", json.dumps(list(options))])
        envelope = self.client.call(
            arguments, mutating=True, request_id=uuid.uuid4().hex,
        )
        result = envelope["result"]
        self._note(record, "orca_gate_created", {
            "gate_id": _result_id(envelope, "gate"),
            "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
        })
        return result

    def gate_resolve(self, binding_id: str, *, gate_id: str,
                     resolution: str) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "gate-resolve")
        gate_id = _identifier(gate_id, "gate id")
        resolution = _closed_text(resolution, "gate resolution", maximum=2_048)
        envelope = self.client.call([
            "orchestration", "gate-resolve", "--id", gate_id,
            "--resolution", resolution,
        ], mutating=True, request_id=uuid.uuid4().hex)
        self._note(record, "orca_gate_resolved", {
            "gate_id": gate_id,
            "resolution_sha256": hashlib.sha256(resolution.encode()).hexdigest(),
        })
        return envelope["result"]

    def gate_list(self, binding_id: str) -> dict[str, Any]:
        record = self._load(binding_id)
        self._permit(record["harness"], "gate-list")
        return self.client.call([
            "orchestration", "gate-list", "--task", record["task_id"],
        ])["result"]


class OrcaExecutionService:
    """Capability-broker handler for an authorized Orca worker start."""

    def __init__(self, provider: OrcaExecutionProvider, *, worktree_root: Path):
        self.provider = provider
        self.worktree_root = Path(worktree_root)

    async def call(self, tool: str, arguments: dict[str, Any],
                   context: dict[str, str]) -> dict[str, Any]:
        if tool != "start" or not isinstance(arguments, dict):
            raise OrcaExecutionError("Orca internal tool is denied")
        allowed = {
            "binding_id", "objective", "task_spec", "model", "effort", "environment",
            "task_id", "goal_id", "contract_sha256", "declared_paths",
        "parallel_binding",
        }
        if set(arguments) - allowed or not {"objective", "task_spec"} <= set(arguments):
            raise OrcaExecutionError("Orca start arguments are not closed")
        values = dict(arguments)
        binding_id = values.pop("binding_id", None)
        if binding_id is None:
            required = {"task_id", "contract_sha256", "declared_paths"}
            if not required <= set(values):
                raise OrcaExecutionError("Orca binding identity is incomplete")
            parallel_binding = values.pop("parallel_binding", False)
            if type(parallel_binding) is not bool:
                raise OrcaExecutionError("Orca parallel binding flag is invalid")
            if parallel_binding:
                admitted = [
                    event for event in self.provider.ledger.events(context["job_id"])
                    if event.get("kind") == "adaptive_provider_selected"
                    and event.get("data", {}).get("provider") == "orca"
                    and event.get("data", {}).get("harness") == context["harness"]
                ]
                if not admitted:
                    raise OrcaExecutionError("parallel Orca binding lacks adaptive authority")
            receipt = self.provider.transport.prepare(
                job_id=context["job_id"], task_id=values.pop("task_id"),
                goal_id=values.pop("goal_id", None),
                contract_sha256=values.pop("contract_sha256"),
                harness=context["harness"],
                declared_paths=values.pop("declared_paths"),
                worktree_root=self.worktree_root,
                allow_parallel=parallel_binding,
                allocator=self.provider.allocate_worktree,
            )
            binding_id = receipt["receipt"]["binding_id"]
        elif set(values) & {"task_id", "goal_id", "contract_sha256", "declared_paths"}:
            raise OrcaExecutionError("Orca binding may not mix prepared and raw identity")
        return self.provider.start(binding_id=binding_id, context=context, **values)


def build_orca_service(*, ledger: JobLedger, allowed_roots, root: Path,
                       switch_path=None, options=None) -> OrcaExecutionService:
    """Provider-neutral factory used by the lazy parallel-provider catalog."""
    options = dict(options or {})
    relative = options.get("adapter_registry", "config/orca-harness-adapters.json")
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise OrcaExecutionError("Orca adapter registry path is invalid")
    roots = tuple(Path(item) for item in allowed_roots)
    if not roots:
        raise OrcaExecutionError("Orca allowed roots are required")
    worktree_root = roots[0] / ".orca-worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)
    provider = OrcaExecutionProvider(
        ledger=ledger,
        transport=WorkerTransport(ledger, orca_harnesses=UNIVERSAL_HARNESS_REGISTRY),
        registry=OrcaHarnessRegistry.load(root / relative),
        client=OrcaCLI(cwd=root), switch_path=switch_path,
    )
    return OrcaExecutionService(provider, worktree_root=worktree_root)


__all__ = [    "HarnessAdapter", "OrcaCLI", "OrcaExecutionError",
    "discover_orca_executable",
    "OrcaExecutionProvider", "OrcaExecutionService", "OrcaHarnessRegistry",
    "build_orca_service",
]
