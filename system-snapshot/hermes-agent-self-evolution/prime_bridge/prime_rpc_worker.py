"""Bounded LF-JSONL adapter for Prime Agent's pinned RPC mode."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any


class PrimeRPCError(RuntimeError):
    pass


class PrimeRPCWorker:
    """Use Prime's own RPC/daemon; do not duplicate its internal harness loop."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        runtime_fingerprint: str,
        environment: dict[str, str] | None = None,
        response_timeout: float = 30.0,
        max_line_bytes: int = 1_048_576,
    ):
        if not command:
            raise PrimeRPCError("Prime RPC command is required")
        if len(runtime_fingerprint) != 64 or any(character not in "0123456789abcdef" for character in runtime_fingerprint):
            raise PrimeRPCError("valid runtime fingerprint is required")
        self.argv = tuple(command)
        self.runtime_fingerprint = runtime_fingerprint
        self.environment = environment
        self.response_timeout = response_timeout
        self.max_line_bytes = max_line_bytes
        self.process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._responses: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @classmethod
    def from_authority(
        cls, authority_path: Path, *, container_id: str,
        environment: dict[str, str] | None = None,
    ) -> PrimeRPCWorker:
        from prime_bridge.job_cli import load_runtime_authority
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        runtime = load_runtime_authority(authority_path, authority=authority)
        command = [container_id if item == "{container_id}" else item for item in authority["command"]]
        return cls(command, runtime_fingerprint=runtime["fingerprint"], environment=environment)

    async def start(self, *, prompt: str, workspace: str, route: dict[str, str]) -> dict[str, str]:
        env = self.environment if self.environment is not None else {
            key: value for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR"}
        }
        self.process = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=Path(workspace),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=self.max_line_bytes + 1,
        )
        self._reader = asyncio.create_task(self._read_loop())
        state = await self._request("get_state")
        await self._request("set_model", provider=route["provider"], modelId=route["model"])
        await self._request("prompt", message=prompt)
        data = state.get("data") or {}
        session_id = data.get("sessionId")
        trace_ref = data.get("sessionFile")
        if not isinstance(session_id, str) or not session_id or not isinstance(trace_ref, str) or not trace_ref:
            raise PrimeRPCError("Prime did not return a session identity")
        return {"session_id": session_id, "trace_ref": trace_ref}

    async def _request(self, kind: str, *, request_id: str | None = None, **fields: Any) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise PrimeRPCError("Prime RPC process is not running")
        request_id = request_id or uuid.uuid4().hex
        if len(request_id) != 32 or any(character not in "0123456789abcdef" for character in request_id):
            raise PrimeRPCError("request ID must be 32 lowercase hex characters")
        if request_id in self._responses:
            raise PrimeRPCError("request ID is already pending")
        future = asyncio.get_running_loop().create_future()
        self._responses[request_id] = future
        message = {"id": request_id, "type": kind, **fields}
        self.process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await self.process.stdin.drain()
        try:
            response = await asyncio.wait_for(future, self.response_timeout)
        finally:
            self._responses.pop(request_id, None)
        if not response.get("success", False):
            raise PrimeRPCError(f"Prime rejected {kind}")
        return response

    async def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                raw = await self.process.stdout.readline()
                if not raw:
                    break
                if len(raw) > self.max_line_bytes or not raw.endswith(b"\n"):
                    raise PrimeRPCError("invalid Prime RPC frame")
                payload = json.loads(raw[:-1].removesuffix(b"\r"))
                if not isinstance(payload, dict):
                    raise PrimeRPCError("Prime RPC frame must be an object")
                request_id = payload.get("id")
                if payload.get("type") == "response" and request_id in self._responses:
                    future = self._responses[request_id]
                    if not future.done(): future.set_result(payload)
                else:
                    await self._events.put(payload)
        except (json.JSONDecodeError, OSError, PrimeRPCError, UnicodeDecodeError, ValueError) as exc:
            for future in self._responses.values():
                if not future.done(): future.set_exception(exc)
        finally:
            await self._events.put({"type": "rpc_closed"})

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._events.get()
            kind = event.get("type")
            if kind == "turn_end":
                yield {"kind": "progress"}
            elif kind == "agent_end":
                messages = event.get("messages")
                assistants = [item for item in messages if isinstance(item, dict) and item.get("role") == "assistant"] if isinstance(messages, list) else []
                if assistants and assistants[-1].get("stopReason") == "error":
                    yield {"kind": "failed", "error": "Prime agent failed"}
                    return
                content = assistants[-1].get("content") if assistants else None
                parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"] if isinstance(content, list) else []
                summary = "\n".join(part for part in parts if part).strip()[:8192] or "Prime agent completed"
                yield {"kind": "completed", "summary": summary}
                return
            elif kind in {"error", "rpc_closed"}:
                yield {"kind": "failed", "error": "Prime RPC closed" if kind == "rpc_closed" else "Prime RPC error"}
                return

    async def command(self, *, command_id: str, kind: str, text: str | None = None) -> None:
        if kind not in {"steer", "abort"}:
            raise PrimeRPCError("unsupported Prime operator command")
        fields = {"message": text} if kind == "steer" else {}
        await self._request(kind, request_id=command_id, **fields)

    async def close(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._reader is not None:
            await self._reader
