"""Pinned OMP SDK sidecar runner that returns proposals, never direct writes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from prime_bridge.inference_relay import InferenceRelay, RelayPolicy
from prime_bridge.omp_singleflight import omp_singleflight_lock


class OmpSdkRunnerError(RuntimeError):
    """Typed sidecar failure with optional content-free accounting evidence."""

    def __init__(self, message: str, *, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.evidence = dict(evidence or {})


_USAGE_INPUT_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens")
_USAGE_LEDGER_KEYS = ("input", "output", "cache_read", "cache_write", "total")


def _normalized_usage(value: object) -> dict[str, int] | None:
    """Keep only non-negative content-free token counters from the sidecar."""
    if not isinstance(value, dict) or set(value) != set(_USAGE_INPUT_KEYS):
        return None
    if any(type(value[key]) is not int or value[key] < 0 for key in _USAGE_INPUT_KEYS):
        return None
    return {
        safe: value[source]
        for safe, source in zip(_USAGE_LEDGER_KEYS, _USAGE_INPUT_KEYS, strict=True)
    }


def _validate_proposal_envelope(item: Any) -> None:
    """Accept the pinned proposal envelope plus optional revision metadata."""
    if not isinstance(item, dict):
        raise OmpSdkRunnerError(f"OMP sidecar proposal is malformed: {[type(item).__name__]}")
    required = {"path", "expected_before_hash", "content"}
    optional = {"operation", "revision", "supersedes_revision"}
    keys = set(item)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise OmpSdkRunnerError(f"OMP sidecar proposal is malformed: {sorted(keys)}")
    has_revision = "revision" in item
    has_supersedes = "supersedes_revision" in item
    if has_revision != has_supersedes:
        raise OmpSdkRunnerError("OMP sidecar proposal revision metadata is incomplete")
    if not has_revision:
        return
    revision = item["revision"]
    supersedes = item["supersedes_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise OmpSdkRunnerError("OMP sidecar proposal revision is invalid")
    if supersedes is not None and (
        isinstance(supersedes, bool)
        or not isinstance(supersedes, int)
        or supersedes < 1
        or supersedes >= revision
    ):
        raise OmpSdkRunnerError("OMP sidecar proposal supersedes_revision is invalid")


def _content_free_process_diagnostic(completed: "_BoundedProcessResult") -> str:
    """Describe sidecar output without retaining or exposing its contents."""
    stdout = completed.stdout.encode("utf-8", errors="replace")
    stderr = completed.stderr.encode("utf-8", errors="replace")
    return (
        f"exit={completed.returncode} "
        f"stdout_bytes={len(stdout)} stdout_sha256={hashlib.sha256(stdout).hexdigest()} "
        f"stderr_bytes={len(stderr)} stderr_sha256={hashlib.sha256(stderr).hexdigest()}"
    )


def load_omp_proposal_policy(repo_root: Path) -> dict[str, int]:
    """Load the bounded runtime policy; malformed policy fails closed."""
    path = Path(repo_root) / "config" / "omp-proposal-policy.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        total = int(value["total_timeout_seconds"])
        no_progress = int(value["no_progress_timeout_seconds"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OmpSdkRunnerError("OMP proposal policy is unavailable") from exc
    if not 0 < no_progress < total:
        raise OmpSdkRunnerError("OMP proposal policy has invalid timeout bounds")
    return {"total_timeout_seconds": total, "no_progress_timeout_seconds": no_progress}


@dataclass(frozen=True)
class _BoundedProcessResult:
    returncode: int
    stdout: str
    stderr: str


async def _run_cleanup_command(command: list[str], timeout_seconds: float) -> None:
    """Run one best-effort cleanup command without inheriting captured pipes."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except (OSError, asyncio.TimeoutError):
        if "process" in locals() and process.returncode is None:
            process.kill()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=1.0)


def _read_container_id(cidfile: Path | None) -> str | None:
    if cidfile is None:
        return None
    try:
        value = cidfile.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        return None
    return value

def _validate_allowed_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Validate a bounded tuple of project-relative POSIX paths.

    Accepts exactly 1 to 8 unique strings. Rejects absolute paths, traversal
    (empty, "." or ".." segments), backslashes, and any dot-git, node_modules,
    dot-env, secrets, or credentials segment. Returns the canonical tuple
    unchanged.
    """
    if not isinstance(paths, tuple):
        raise TypeError("allowed paths must be a tuple")
    if not 1 <= len(paths) <= 8:
        raise ValueError("allowed paths must contain between 1 and 8 entries")
    if len(set(paths)) != len(paths):
        raise ValueError("allowed paths must be unique")
    for path in paths:
        if not isinstance(path, str) or not path:
            raise ValueError("allowed paths must be non-empty strings")
        if path.startswith("/"):
            raise ValueError("allowed paths must be project-relative")
        if "\\" in path:
            raise ValueError("allowed paths must use POSIX separators")
        for segment in path.split("/"):
            if segment in {"", ".", ".."}:
                raise ValueError("allowed paths must not traverse or contain empty segments")
            if segment in {".git", "node_modules", ".env", "secrets", "credentials"}:
                raise ValueError("allowed paths must not contain a restricted segment")
    return paths


def _timeout_progress_summary(progress_path: Path | None) -> str:
    """Return a bounded, content-free classification from typed sidecar progress."""
    value: dict[str, Any] = {}
    if progress_path is not None:
        try:
            loaded = json.loads(progress_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                value = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    tool_calls = value.get("tool_calls", 0)
    tool_count = tool_calls if isinstance(tool_calls, int) and tool_calls >= 0 else 0
    progress = value.get("message_progress")
    last_event = "none"
    if isinstance(progress, dict):
        candidate = progress.get("last_event_kind")
        if isinstance(candidate, str) and candidate in {
            "startup", "message_start", "text_delta", "thinking_delta",
            "message_end", "tool_execution_start", "tool_execution_end",
        }:
            last_event = candidate
    classification = "PRE_TOOL_MODEL_STALL" if tool_count == 0 else "TOOL_LOOP_STALL"
    assistant_error = str(value.get("assistant_error") or "")[:512]
    suffix = f" assistant_error={assistant_error}" if assistant_error else ""
    return f"classification={classification} last_event_kind={last_event} tool_calls={tool_count}{suffix}"


async def _terminate_bounded_process(
    process: asyncio.subprocess.Process,
    *,
    cidfile: Path | None,
    podman_binary: str,
    cleanup_timeout_seconds: float,
) -> None:
    """Terminate the exact container and the local launcher process group."""
    container_id = _read_container_id(cidfile)
    if container_id is not None:
        await _run_cleanup_command(
            [podman_binary, "kill", "--signal", "KILL", container_id],
            cleanup_timeout_seconds,
        )
        await _run_cleanup_command(
            [podman_binary, "rm", "--force", container_id],
            cleanup_timeout_seconds,
        )
    if process.returncode is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=cleanup_timeout_seconds)


async def _run_bounded_process(
    command: list[str],
    *,
    timeout_seconds: float,
    no_progress_seconds: float | None = None,
    cleanup_timeout_seconds: float = 8.0,
    cancellation: asyncio.Event | None = None,
    cidfile: Path | None = None,
    progress_path: Path | None = None,
    podman_binary: str = "podman",
) -> _BoundedProcessResult:
    """Run a command with a real wall-clock bound and descendant cleanup."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise OmpSdkRunnerError("OMP sidecar launch failed") from exc

    output_task = asyncio.create_task(process.communicate())
    cancellation_task = (
        asyncio.create_task(cancellation.wait()) if cancellation is not None else None
    )
    waiters = {output_task}
    if cancellation_task is not None:
        waiters.add(cancellation_task)
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_progress = started
    progress_mtime: int | None = None
    try:
        while True:
            now = loop.time()
            if progress_path is not None:
                try:
                    observed_mtime = progress_path.stat().st_mtime_ns
                except OSError:
                    observed_mtime = None
                if observed_mtime is not None and observed_mtime != progress_mtime:
                    progress_mtime = observed_mtime
                    last_progress = now
            total_remaining = timeout_seconds - (now - started)
            progress_remaining = (
                (no_progress_seconds - (now - last_progress))
                if no_progress_seconds is not None else total_remaining
            )
            if total_remaining <= 0 or progress_remaining <= 0:
                await _terminate_bounded_process(
                    process, cidfile=cidfile, podman_binary=podman_binary,
                    cleanup_timeout_seconds=cleanup_timeout_seconds,
                )
                output_task.cancel()
                with suppress(asyncio.CancelledError):
                    await output_task
                reason = "TOTAL_BUDGET" if total_remaining <= 0 else "NO_PROGRESS"
                progress_summary = _timeout_progress_summary(progress_path)
                raise OmpSdkRunnerError(
                    f"OMP sidecar terminal_state=TIMEOUT reason={reason} {progress_summary}"
                )
            done, _pending = await asyncio.wait(
                waiters, timeout=min(total_remaining, progress_remaining, 1.0),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done:
                break
        if cancellation_task is not None and cancellation_task in done:
            await _terminate_bounded_process(
                process,
                cidfile=cidfile,
                podman_binary=podman_binary,
                cleanup_timeout_seconds=cleanup_timeout_seconds,
            )
            output_task.cancel()
            with suppress(asyncio.CancelledError):
                await output_task
            raise asyncio.CancelledError("OMP sidecar cancelled")
        stdout, stderr = output_task.result()
        return _BoundedProcessResult(
            returncode=int(process.returncode or 0),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )
    except asyncio.CancelledError:
        await _terminate_bounded_process(
            process,
            cidfile=cidfile,
            podman_binary=podman_binary,
            cleanup_timeout_seconds=cleanup_timeout_seconds,
        )
        output_task.cancel()
        with suppress(asyncio.CancelledError):
            await output_task
        raise
    finally:
        if cancellation_task is not None:
            cancellation_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation_task


@dataclass(frozen=True)
class OmpEditProposal:
    """An SDK-generated full-file replacement bound to source bytes."""

    relative_path: str
    expected_before_hash: str
    proposed_bytes: bytes
    operation: str = "replace"


class OmpProposalRunner(Protocol):
    async def propose(
        self, *, prompt: str, project_root: Path, model_id: str,
        provider_id: str = "hermes-ollama",
        profile: str = "workspace",
        allowed_paths: tuple[str, ...], cancellation: asyncio.Event,
    ) -> tuple[list[OmpEditProposal], dict[str, Any]]: ...


@dataclass(frozen=True)
class OmpSdkSidecarRunner:
    """Run the pinned SDK in a rootless sidecar through the Hermes relay.

    The sidecar can only read a read-only source mount and emit proposals into
    a temporary output mount. The caller remains the sole writer of the
    disposable worktree.
    """

    repo_root: Path
    image: str
    timeout_seconds: int = 180
    no_progress_seconds: int = 60
    max_tool_calls: int = 30

    @classmethod
    def from_policy(cls, repo_root: Path, image: str, *, max_tool_calls: int | None = None,
                    max_wall_seconds: int | None = None) -> "OmpSdkSidecarRunner":
        policy = load_omp_proposal_policy(repo_root)
        return cls(
            repo_root=repo_root,
            image=image,
            timeout_seconds=min(policy["total_timeout_seconds"], max_wall_seconds or policy["total_timeout_seconds"]),
            no_progress_seconds=policy["no_progress_timeout_seconds"],
            max_tool_calls=max_tool_calls or 30,
        )

    async def propose(
        self, *, prompt: str, project_root: Path, model_id: str,
        provider_id: str = "hermes-ollama",
        profile: str = "workspace",
        allowed_paths: tuple[str, ...], cancellation: asyncio.Event,
    ) -> tuple[list[OmpEditProposal], dict[str, Any]]:
        allowed_paths = _validate_allowed_paths(allowed_paths)
        if provider_id == "openai-codex":
            raise OmpSdkRunnerError(
                "CHILD_DISPATCH_FAILURE: pinned OMP runtime has no verified subscription-backed "
                "Codex Responses gateway; native continuation is permitted only by the shared policy "
                "and remains disabled for fail-closed execution"
            )
        if cancellation.is_set():
            raise asyncio.CancelledError("OMP worker cancelled before sidecar launch")
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise OmpSdkRunnerError("OMP source root is unavailable")
        script_dir = Path(self.repo_root).resolve() / "scripts" / "omp"
        sidecar = script_dir / "brokered-edit-sidecar.mjs"
        relay_client = script_dir / "relay-client.mjs"
        if not sidecar.is_file() or not relay_client.is_file():
            raise OmpSdkRunnerError("OMP sidecar scripts are unavailable")
        with omp_singleflight_lock(), tempfile.TemporaryDirectory(prefix="omp-sdk-") as temporary:
            temp = Path(temporary)
            request_path = temp / "request.json"
            result_path = temp / "result.json"
            relay_socket = temp / "relay.sock"
            cidfile = temp / "container.cid"
            podman_binary = "/usr/bin/podman" if Path("/usr/bin/podman").is_file() else "podman"
            request_path.write_text(
                json.dumps({
                    "prompt": prompt,
                    "provider": provider_id,
                    "model": model_id,
                    "allowed_paths": list(allowed_paths),
                    "max_tool_calls": self.max_tool_calls,
                }),
                encoding="utf-8",
            )
            relay = InferenceRelay(relay_socket, RelayPolicy("127.0.0.1", ("127.0.0.1",), 11434))
            await relay.start()
            try:
                completed = await _run_bounded_process(
                    [
                        podman_binary, "run", "--rm", "--cidfile", str(cidfile),
                        "--network", "none", "--read-only", "--tmpfs", "/tmp",
                        "--mount", f"type=bind,src={relay_socket},target=/run/prime-relay/relay.sock,ro=true",
                        "--mount", f"type=bind,src={root},target=/workspace/source,ro=true",
                        "--mount", f"type=bind,src={request_path},target=/run/config/request.json,ro=true",
                        "--mount", f"type=bind,src={temp},target=/run/output,rw=true",
                        "--mount", f"type=bind,src={relay_client},target=/opt/omp/relay-client.mjs,ro=true",
                        "--mount", f"type=bind,src={sidecar},target=/opt/omp/brokered-edit-sidecar.mjs,ro=true",
                        "--entrypoint", "sh", self.image, "-c",
                        "bun /opt/omp/relay-client.mjs & sleep 1; bun /opt/omp/brokered-edit-sidecar.mjs --request /run/config/request.json --output /run/output/result.json",
                    ],
                    timeout_seconds=self.timeout_seconds,
                    no_progress_seconds=self.no_progress_seconds,
                    cancellation=cancellation,
                    cidfile=cidfile,
                    progress_path=result_path,
                    podman_binary=podman_binary,
                )
            finally:
                await relay.close()
            if cancellation.is_set():
                raise asyncio.CancelledError("OMP worker cancelled after sidecar launch")
            try:
                value = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                diagnostic = _content_free_process_diagnostic(completed)
                raise OmpSdkRunnerError(
                    f"OMP sidecar did not return a typed result; {diagnostic}"
                ) from exc
            if completed.returncode != 0 or value.get("status") != "PASS":
                terminal_state = str(value.get("terminal_state") or "TERMINAL_FAIL")
                reason = str(value.get("error") or "sidecar reported non-pass status")[:512]
                code = str(value.get("error_code") or "SIDECAR_PROTOCOL_FAILURE")
                recovery = str(value.get("suggested_recovery") or "retry once with the exact declared paths")[:256]
                usage = _normalized_usage(value.get("usage"))
                model_requests = value.get("model_requests")
                if type(model_requests) is not int or model_requests < 0:
                    model_requests = None
                raise OmpSdkRunnerError(
                    f"OMP sidecar error_code={code} terminal_state={terminal_state} failed closed: {reason}; "
                    f"suggested_recovery={recovery}; exit={completed.returncode}; "
                    f"tool_calls={value.get('tool_calls')}; usage_seen={value.get('usage_seen')}; "
                    f"tools={','.join(map(str, value.get('tool_inventory', [])))[:120]}; "
                    f"assistant={str(value.get('assistant_text_excerpt') or '')[:512]}; "
                    f"assistant_error={str(value.get('assistant_error') or '')[:512]}; "
                    f"attempted={json.dumps(value.get('attempted_tools', []), sort_keys=True)[:768]}; "
                    f"errors={json.dumps(value.get('tool_errors', []), sort_keys=True)[:512]}",
                    evidence={
                        "usage_seen": value.get("usage_seen") is True,
                        "usage": usage,
                        "model_requests": model_requests,
                        "provider_calls": 1 if (model_requests or 0) > 0 or usage is not None else None,
                        "tool_calls": value.get("tool_calls"),
                    },
                )
            raw_proposals = value.get("proposals")
            if not isinstance(raw_proposals, list) or not raw_proposals:
                raise OmpSdkRunnerError("OMP sidecar returned no edit proposal")
            if len(raw_proposals) > len(allowed_paths):
                raise OmpSdkRunnerError("OMP sidecar returned more proposals than allowed paths")
            allowed_set = set(allowed_paths)
            seen_paths: set[str] = set()
            total_content_bytes = 0
            empty_source_hash = hashlib.sha256(b"").hexdigest()
            proposals: list[OmpEditProposal] = []
            for item in raw_proposals:
                _validate_proposal_envelope(item)
                path, before_hash, content = item["path"], item["expected_before_hash"], item["content"]
                if not isinstance(path, str) or not isinstance(before_hash, str) or not isinstance(content, str):
                    raise OmpSdkRunnerError("OMP sidecar proposal has invalid types")
                operation = item.get("operation", "replace")
                if operation not in {"replace", "create"}:
                    raise OmpSdkRunnerError("OMP sidecar proposal operation is invalid")
                if path not in allowed_set:
                    raise OmpSdkRunnerError("OMP sidecar proposal targets a path outside allowed_paths")
                if path in seen_paths:
                    raise OmpSdkRunnerError("OMP sidecar proposal contains a duplicate path")
                seen_paths.add(path)
                if (
                    len(before_hash) != 64
                    or any(character not in "0123456789abcdef" for character in before_hash)
                ):
                    raise OmpSdkRunnerError("OMP sidecar proposal anchor is not 64-lowercase-hex")
                if "\x00" in content:
                    raise OmpSdkRunnerError("OMP sidecar proposal content contains NUL bytes")
                encoded = content.encode("utf-8")
                if len(encoded) > 65536:
                    raise OmpSdkRunnerError("OMP sidecar proposal content exceeds 65536 UTF-8 bytes")
                total_content_bytes += len(encoded)
                if total_content_bytes > 131072:
                    raise OmpSdkRunnerError("OMP sidecar proposal content exceeds 131072 total UTF-8 bytes")
                target = root / path
                if operation == "create":
                    if target.exists():
                        raise OmpSdkRunnerError("OMP sidecar create proposal targets an existing path")
                    if before_hash != empty_source_hash:
                        raise OmpSdkRunnerError("OMP sidecar create proposal anchor is not the empty-source hash")
                else:
                    try:
                        current_bytes = target.read_bytes()
                    except OSError:
                        raise OmpSdkRunnerError("OMP sidecar replace proposal targets an absent source file")
                    if hashlib.sha256(current_bytes).hexdigest() != before_hash:
                        raise OmpSdkRunnerError("OMP sidecar replace proposal anchor differs from current source bytes")
                    if content == current_bytes.decode("utf-8", errors="replace"):
                        raise OmpSdkRunnerError("OMP sidecar replace proposal content is a no-op")
                proposals.append(OmpEditProposal(path, before_hash, encoded, operation))
            evidence = {
                "sdk": "@oh-my-pi/pi-coding-agent@17.3.0",
                "tool_calls": value.get("tool_calls", 0),
                "usage_seen": value.get("usage_seen") is True,
                "usage": _normalized_usage(value.get("usage")),
                "model_requests": value.get("model_requests"),
                "provider_calls": 1,
                "proposal_count": len(proposals),
                "allowed_path_count": len(allowed_paths),
                "allowed_path_set_hash": hashlib.sha256(
                    json.dumps(sorted(allowed_paths), separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "result_hash": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(),
            }
            return proposals, evidence
