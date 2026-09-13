#!/usr/bin/env python3
"""Permanent sealed Herdr reviewer bridge for the ChatGPT-backed Codex CLI."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

MAX_REQUEST_BYTES = 1_048_576
MAX_FINDINGS = 4
ALLOWED_REASONING = {"low", "medium", "high"}
TOOL_MARKERS = ("command", "tool", "shell", "file_change", "web_search")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def walk(value: Any, usage: list[tuple[int, int]], event_types: list[str]) -> int:
    tools = 0
    if isinstance(value, dict):
        input_units = value.get("input_tokens", value.get("prompt_tokens"))
        output_units = value.get("output_tokens", value.get("completion_tokens"))
        if isinstance(input_units, int) and isinstance(output_units, int):
            usage.append((input_units, output_units))
        event_type = str(value.get("type", ""))
        if event_type:
            event_types.append(event_type)
            tools += int(any(marker in event_type.lower() for marker in TOOL_MARKERS))
        for child in value.values():
            tools += walk(child, usage, event_types)
    elif isinstance(value, list):
        for child in value:
            tools += walk(child, usage, event_types)
    return tools


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning", choices=sorted(ALLOWED_REASONING), default="low")
    parser.add_argument("--codex-executable", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    args = parser.parse_args()

    raw_request = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if not raw_request or len(raw_request) > MAX_REQUEST_BYTES:
        raise SystemExit("sealed request is absent or exceeds the bridge limit")
    request = json.loads(raw_request)
    required = {
        "schema_version", "receipt_id", "attempt_id", "reviewer_id", "s_level",
        "packet_sha256", "source_identity", "source_hashes", "rubric", "content",
        "deadline_ms", "reserved_budget", "max_tokens",
    }
    if not isinstance(request, dict) or set(request) != required:
        raise SystemExit("sealed request envelope is invalid")
    if request["schema_version"] != "1.0" or not 1 <= request["max_tokens"] <= 2048:
        raise SystemExit("sealed request version or output budget is invalid")

    codex = args.codex_executable.resolve()
    codex_home = args.codex_home.resolve()
    if (
        not codex.is_file()
        or not codex.name.lower().startswith("codex")
        or not codex_home.is_dir()
    ):
        raise SystemExit("pinned Codex runtime or subscription home is unavailable")
    root = Path.cwd()
    schema_path = root / "review-output.schema.json"
    final_path = root / "review-output.json"
    diagnostic_path = root / "diagnostic.json"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "findings": {
                "type": "array",
                "maxItems": MAX_FINDINGS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
                        "category": {"type": "string"},
                        "title": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "advisory": {"type": "boolean"},
                    },
                    "required": ["finding_id", "severity", "category", "title", "evidence", "advisory"],
                },
            }
        },
        "required": ["findings"],
    }
    schema_path.write_text(json.dumps(schema, sort_keys=True), encoding="utf-8")
    prompt = (
        "Act as a report-only code evaluator. Do not run tools, commands, web searches, "
        "or inspect the filesystem. Use only the sealed request below. Return one "
        "schema-matching JSON object. Do not emit patches or commands. Evidence may cite "
        "only supplied paths and facts. Empty findings is allowed.\n"
        + json.dumps(request, sort_keys=True, separators=(",", ":"))
    )
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    started_at = utc_now()
    command = [
        str(codex), "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
        "-m", args.model, "-c", f'model_reasoning_effort="{args.reasoning}"',
        "--output-schema", str(schema_path), "-o", str(final_path), "-C", str(root), "-",
    ]
    try:
        result = subprocess.run(
            command, input=prompt, text=True, capture_output=True, env=environment,
            timeout=min(240, max(1, request["deadline_ms"] // 1000)), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        diagnostic_path.write_text(json.dumps({
            "returncode": None,
            "timed_out": True,
            "event_types": [],
            "usage_found": False,
            "tool_event_count": 0,
            "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        }, sort_keys=True) + "\n", encoding="utf-8")
        raise SystemExit("reviewer provider timed out without a settled result")
    usage: list[tuple[int, int]] = []
    event_types: list[str] = []
    tool_events = 0
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        tool_events += walk(event, usage, event_types)
    diagnostic_path.write_text(json.dumps({
        "returncode": result.returncode,
        "event_types": event_types,
        "usage_found": bool(usage),
        "tool_event_count": tool_events,
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
    }, sort_keys=True) + "\n", encoding="utf-8")
    if result.returncode or tool_events or not usage or not final_path.is_file():
        raise SystemExit("reviewer provider did not produce a safe complete result")
    findings = json.loads(final_path.read_text(encoding="utf-8"))["findings"]
    input_units, output_units = usage[-1]
    response = {
        "schema_version": "1.0",
        "receipt_id": request["receipt_id"],
        "attempt_id": request["attempt_id"],
        "reviewer_id": request["reviewer_id"],
        "packet_sha256": request["packet_sha256"],
        "source_identity": request["source_identity"],
        "findings": findings,
        "finish_reason": "stop",
        "usage": {
            "prompt_tokens": input_units,
            "completion_tokens": output_units,
            "total_tokens": input_units + output_units,
        },
        "started_at": started_at,
        "finished_at": utc_now(),
    }
    sys.stdout.write(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
