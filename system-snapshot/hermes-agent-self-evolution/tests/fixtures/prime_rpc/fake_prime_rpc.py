"""Adversarial Prime v0.7.1 JSONL-RPC fixture executed only in Podman."""

from __future__ import annotations

import json
import sys
import time


mode = sys.argv[1]
session_id = "fixture-session-01"
provider = "hermes-ollama"
model_id = "fixture-model"
api = "openai-completions"
endpoint = "https://fixture.invalid/v1"
tool_call_id = "call-ipython-01"


def model() -> dict:
    value = {"provider": provider, "id": model_id, "api": api, "baseUrl": endpoint}
    if mode == "route_mismatch":
        value["id"] = "substituted-model"
    if mode == "api_unobservable":
        value.pop("api")
    if mode == "endpoint_unobservable":
        value.pop("baseUrl")
    return value


def emit(frame: dict, *, ending: bytes | None = None) -> None:
    delimiter = b"\r\n" if mode == "crlf" else b"\n"
    sys.stdout.buffer.write(
        json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + (delimiter if ending is None else ending)
    )
    sys.stdout.buffer.flush()


def response(request: dict, data=None) -> None:
    response_id = "wrong-id" if mode == "response_id" else request["id"]
    frame = {
        "id": response_id,
        "type": "response",
        "command": request["type"],
        "success": True,
    }
    if data is not None:
        frame["data"] = data
    emit(frame)


for raw in sys.stdin.buffer:
    request = json.loads(raw.decode("utf-8"))
    command = request["type"]
    if mode == "malformed":
        sys.stdout.buffer.write(b"{not-json\n")
        sys.stdout.buffer.flush()
        time.sleep(60)
    if mode == "bare_cr":
        first = {"id": request["id"], "type": "response", "command": command, "success": True}
        second = {"type": "agent_start"}
        sys.stdout.buffer.write(
            json.dumps(first, separators=(",", ":")).encode() + b"\r"
            + json.dumps(second, separators=(",", ":")).encode() + b"\n"
        )
        sys.stdout.buffer.flush()
        time.sleep(60)
    if mode == "crash":
        raise SystemExit(17)
    if mode in {"stall", "cancel"}:
        time.sleep(60)

    if command == "get_state":
        final = getattr(sys.modules[__name__], "prompt_seen", False)
        current_session = "substituted-session" if mode == "session_substitution" and final else session_id
        response(request, {
            "model": model(),
            "isStreaming": False,
            "sessionId": current_session,
            "messageCount": 1 if final else 0,
        })
    elif command == "get_available_models":
        response(request, {"models": [model()]})
    elif command == "prompt":
        prompt_seen = True
        response(request)
        start = {"type": "agent_start"}
        if mode == "event_id":
            start["id"] = request["id"]
        if mode == "event_session_substitution":
            start["sessionId"] = "other-session"
        emit(start)
        emit({
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": "Unicode stays inside JSON: \u2028 and \u2029; token=sentinel-runtime-credential",
            },
        })
        start_id = "wrong-start-id" if mode == "tool_id" else tool_call_id
        start_name = "bash" if mode == "tool_name" else "ipython"
        emit({
            "type": "tool_execution_start",
            "toolCallId": start_id,
            "toolName": start_name,
            "args": {"code": "2 + 2"},
        })
        if mode == "double_tool":
            emit({
                "type": "tool_execution_start",
                "toolCallId": "call-ipython-02",
                "toolName": "ipython",
                "args": {"code": "2 + 2"},
            })
        emit({
            "type": "tool_execution_update",
            "toolCallId": start_id,
            "toolName": start_name,
            "args": {"code": "2 + 2"},
            "partialResult": {"content": [{"type": "text", "text": "4"}]},
        })
        end_id = "wrong-end-id" if mode == "tool_id" else tool_call_id
        end_name = "python" if mode == "tool_name" else "ipython"
        value = "5" if mode == "wrong_result" else "4"
        emit({
            "type": "tool_execution_end",
            "toolCallId": end_id,
            "toolName": end_name,
            "result": {"content": [{"type": "text", "text": value}], "details": {"status": "ok"}},
            "isError": mode == "tool_error",
        })
        emit({
            "type": "agent_end",
            "messages": [{"role": "assistant", "content": "PRIME_RPC_OK:4"}],
        })
    elif command == "get_session_stats":
        response(request, {
            "sessionId": session_id,
            "tokens": {"input": 5, "output": 2, "total": 7},
            "toolCalls": 1,
            "toolResults": 1,
        })
    elif command == "get_last_assistant_text":
        text = "not-the-exact-result" if mode == "wrong_final" else "PRIME_RPC_OK:4"
        response(request, {"text": text})
    elif command == "abort":
        response(request)
        raise SystemExit(0)
    else:
        emit({
            "id": request["id"],
            "type": "response",
            "command": command,
            "success": False,
            "error": "unsupported",
        })
