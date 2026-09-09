"""Bounded LF-JSONL adapter for Prime Agent's pinned RPC mode."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
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
        trajectory_capture: Any | None = None,
        max_total_tokens: int | None = None,
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
        self.trajectory_capture = trajectory_capture
        if max_total_tokens is not None and (type(max_total_tokens) is not int or max_total_tokens < 1):
            raise PrimeRPCError("max_total_tokens must be a positive integer")
        self.max_total_tokens = max_total_tokens
        self.process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._responses: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._trajectory_session_id = ""
        self._trajectory_route: dict[str, str] = {}
        self._trajectory_messages: list[dict[str, Any]] = []
        self._trajectory_turn = 0
        self._trajectory_request_open = False
        self._trajectory_responses = 0
        self._trajectory_tool_calls = 0

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
        self._trajectory_route = dict(route)
        self._trajectory_messages = [{"role": "user", "content": prompt}]
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
        self._trajectory_session_id = session_id
        return {"session_id": session_id, "trace_ref": trace_ref}

    @staticmethod
    def _message_text(message: Any) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text") or "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
        return ""

    def _capture_request(self) -> None:
        capture = self.trajectory_capture
        if capture is None or self._trajectory_request_open:
            return
        self._trajectory_turn += 1
        boundary = f"prime-rpc:{self._trajectory_session_id or 'opening'}:request:{self._trajectory_turn}"
        request = {
            "messages": list(self._trajectory_messages),
            "source": "prime_rpc_visible_transcript",
            "provider_wire_payload_exposed": False,
        }
        arguments = {
            "boundary_id": boundary,
            "turn_id": f"prime-turn:{self._trajectory_turn}",
            "request": request,
            "tools": (),
            "provider": self._trajectory_route.get("provider", "unknown"),
            "model": self._trajectory_route.get("model", "unknown"),
            "api_mode": "prime-rpc-v0.7.1-model-visible",
            "message_count": len(self._trajectory_messages),
            "tool_count": self._trajectory_tool_calls,
        }
        if self._trajectory_turn == 1:
            capture.model_request(
                **arguments,
                user_input=str(self._trajectory_messages[0]["content"]),
                system_prompt="",
            )
        else:
            capture.model_request_boundary(**arguments)
        self._trajectory_request_open = True

    @staticmethod
    def _normalized_model_usage(
        value: Any, *, session_id: str,
    ) -> dict[str, int | str] | None:
        """Keep only exact, content-free counters reported by Prime RPC."""
        if not isinstance(value, Mapping) or value.get("sessionId") not in (None, session_id):
            return None
        counters = value.get("tokens")
        if not isinstance(counters, Mapping):
            return None
        input_units = counters.get("input")
        output_units = counters.get("output")
        cache_read_units = counters.get("cacheRead", 0)
        cache_write_units = counters.get("cacheWrite", 0)
        total_units = counters.get("total")
        if (
            type(input_units) is not int or input_units < 0
            or type(output_units) is not int or output_units < 0
            or type(cache_read_units) is not int or cache_read_units < 0
            or type(cache_write_units) is not int or cache_write_units < 0
            or type(total_units) is not int or total_units < 0
            or total_units != input_units + output_units + cache_read_units + cache_write_units
        ):
            return None
        return {
            "input_units": input_units,
            "output_units": output_units,
            "cache_read_units": cache_read_units,
            "cache_write_units": cache_write_units,
            "total_units": total_units,
            "source": "prime_rpc_session_stats",
        }

    async def _session_usage(self) -> dict[str, int | str] | None:
        if (
            not self._trajectory_session_id
            or self.process is None
            or getattr(self.process, "stdin", None) is None
        ):
            return None
        try:
            response = await self._request("get_session_stats")
        except (PrimeRPCError, asyncio.TimeoutError, OSError):
            return None
        return self._normalized_model_usage(
            response.get("data"), session_id=self._trajectory_session_id,
        )

    def _capture_rpc_event(self, event: dict[str, Any]) -> None:
        capture = self.trajectory_capture
        if capture is None:
            return
        kind = event.get("type")
        if kind in {"agent_start", "message_start"}:
            self._capture_request()
            return
        if kind == "message_end":
            message = event.get("message")
            text = self._message_text(message)
            if isinstance(message, dict) and message.get("role") == "assistant" and text:
                self._capture_request()
                self._trajectory_responses += 1
                capture.model_response(
                    boundary_id=(
                        f"prime-rpc:{self._trajectory_session_id or 'opening'}:"
                        f"response:{self._trajectory_responses}"
                    ),
                    turn_id=f"prime-turn:{self._trajectory_turn}",
                    content=text,
                    finish_reason=str(message.get("stopReason") or "message_end"),
                    response_model=self._trajectory_route.get("model"),
                    duration_ms=0,
                    # Prime exposes cumulative session usage only after the
                    # agent reaches its idle terminal boundary. Do not block
                    # event delivery or invent per-response attribution.
                    usage={},
                    tool_call_count=self._trajectory_tool_calls,
                )
                self._trajectory_messages.append({"role": "assistant", "content": text})
                self._trajectory_request_open = False
            return
        if kind == "tool_execution_start":
            call_id = str(event.get("toolCallId") or f"uncorrelated-{self._trajectory_tool_calls + 1}")
            tool_name = str(event.get("toolName") or "unknown")
            arguments = event.get("args") if isinstance(event.get("args"), dict) else {}
            self._trajectory_tool_calls += 1
            capture.tool_call(
                boundary_id=call_id, turn_id=f"prime-turn:{self._trajectory_turn}",
                tool_name=tool_name, arguments=arguments,
            )
            self._trajectory_messages.append({
                "role": "assistant", "tool_call_id": call_id,
                "tool": tool_name, "arguments": arguments,
            })
            return
        if kind == "tool_execution_end":
            call_id = str(event.get("toolCallId") or "uncorrelated")
            tool_name = str(event.get("toolName") or "unknown")
            result = event.get("result")
            capture.tool_result(
                boundary_id=call_id, turn_id=f"prime-turn:{self._trajectory_turn}",
                tool_name=tool_name, result=result,
                status="error" if event.get("isError") is True else "completed",
                duration_ms=0,
            )
            self._trajectory_messages.append({
                "role": "tool", "tool_call_id": call_id, "tool": tool_name,
                "content": result,
            })

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
            self._capture_rpc_event(event)
            if kind == "turn_end":
                if self.max_total_tokens is not None:
                    model_usage = await self._session_usage()
                    if model_usage is None:
                        try:
                            await self._request("abort")
                        except (PrimeRPCError, asyncio.TimeoutError, OSError):
                            pass
                        yield {
                            "kind": "failed",
                            "error": "Prime token budget accounting unavailable",
                        }
                        return
                    if model_usage["total_units"] >= self.max_total_tokens:
                        try:
                            await self._request("abort")
                        except (PrimeRPCError, asyncio.TimeoutError, OSError):
                            pass
                        yield {
                            "kind": "failed",
                            "error": "Prime token budget exhausted",
                            "model_usage": model_usage,
                        }
                        return
                yield {"kind": "progress"}
            elif kind == "agent_end":
                messages = event.get("messages")
                assistants = [item for item in messages if isinstance(item, dict) and item.get("role") == "assistant"] if isinstance(messages, list) else []
                if assistants and assistants[-1].get("stopReason") == "error":
                    failed = {"kind": "failed", "error": "Prime agent failed"}
                    model_usage = await self._session_usage()
                    if model_usage is not None:
                        failed["model_usage"] = model_usage
                    yield failed
                    return
                model_usage = await self._session_usage()
                content = assistants[-1].get("content") if assistants else None
                if isinstance(content, str):
                    parts = [content]
                elif isinstance(content, list):
                    parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
                else:
                    parts = []
                summary = "\n".join(part for part in parts if part).strip()[:8192] or "Prime agent completed"
                if (
                    self.trajectory_capture is not None
                    and self._trajectory_responses == 0
                    and summary != "Prime agent completed"
                ):
                    self._capture_request()
                    self._trajectory_responses += 1
                    self.trajectory_capture.model_response(
                        boundary_id=f"prime-rpc:{self._trajectory_session_id or 'opening'}:response:1",
                        turn_id=f"prime-turn:{self._trajectory_turn}", content=summary,
                        finish_reason="agent_end", response_model=self._trajectory_route.get("model"),
                        duration_ms=0, usage=(
                            {
                                "input_tokens": model_usage["input_units"],
                                "output_tokens": model_usage["output_units"],
                                "total_tokens": model_usage["total_units"],
                            }
                            if model_usage is not None else {}
                        ),
                        tool_call_count=self._trajectory_tool_calls,
                    )
                completed = {"kind": "completed", "summary": summary}
                if model_usage is not None:
                    completed["model_usage"] = model_usage
                yield completed
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
