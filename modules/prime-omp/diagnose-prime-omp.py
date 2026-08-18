#!/usr/bin/env python3
"""Diagnose Prime/OMP without confusing config, process, session, and runtime state."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


PRIME_TOOL_PREFIXES = ("prime_", "mcp_prime_")


def _run(
    command: list[str], timeout: int, *, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def _read_session_tools(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("tools")
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("session tools JSON must be a string list or an object with a tools list")
    return value


def _parse_last_json(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def classify(
    *, prime_returncode: int, prime_output: str,
    session_tools: list[str] | None, omp_verdict: str | None,
) -> dict[str, Any]:
    """Create a typed diagnosis from independently observed boundaries."""
    prime_connected = (
        prime_returncode == 0
        and "connected" in prime_output.casefold()
        and "tools discovered" in prime_output.casefold()
    )
    if session_tools is None:
        session_status = "NOT_CHECKED"
    elif any(name.startswith(PRIME_TOOL_PREFIXES) for name in session_tools):
        session_status = "VISIBLE"
    elif prime_connected:
        session_status = "STALE"
    else:
        session_status = "UNAVAILABLE"

    if not prime_connected:
        action = "CHECK_PRIME_MCP_CONFIG_OR_HANDSHAKE"
    elif session_status == "STALE":
        action = "RELOAD_MCP_AND_OPEN_NEW_SESSION"
    elif omp_verdict not in {None, "READY"}:
        action = "REPAIR_OMP_FROM_CANONICAL_PROBE"
    else:
        action = "NONE"

    return {
        "schema_version": 1,
        "prime_mcp": "CONNECTED" if prime_connected else "UNAVAILABLE",
        "session_tools": session_status,
        "omp_runtime": omp_verdict or "NOT_CHECKED",
        "next_action": action,
        "facts": {
            "tool_search_is_availability_authority": False,
            "runtime_lock_is_live_status": False,
        },
    }


def selftest() -> int:
    connected = "Connected\nTools discovered: 27"
    cases = {
        "healthy": classify(
            prime_returncode=0, prime_output=connected,
            session_tools=["prime_delegate"], omp_verdict="READY",
        )["next_action"] == "NONE",
        "stale-session": classify(
            prime_returncode=0, prime_output=connected,
            session_tools=["memory_recall"], omp_verdict="READY",
        )["next_action"] == "RELOAD_MCP_AND_OPEN_NEW_SESSION",
        "closed-handshake": classify(
            prime_returncode=1, prime_output="Connection closed",
            session_tools=None, omp_verdict=None,
        )["next_action"] == "CHECK_PRIME_MCP_CONFIG_OR_HANDSHAKE",
        "lock-is-not-status": classify(
            prime_returncode=0, prime_output=connected,
            session_tools=None, omp_verdict="RUNTIME_UNAVAILABLE",
        )["next_action"] == "REPAIR_OMP_FROM_CANONICAL_PROBE",
    }
    failures = [name for name, passed in cases.items() if not passed]
    print(json.dumps({"cases": len(cases), "failures": failures}, sort_keys=True))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-bin", default="hermes")
    parser.add_argument("--profile", default="workspace")
    parser.add_argument("--session-tools-json", type=Path)
    parser.add_argument("--omp-probe", type=Path)
    parser.add_argument("--omp-python", help="Python with the OMP probe dependencies")
    parser.add_argument("--omp-runtime-path", help="PATH used by the OMP feasibility probe")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    prime = _run(
        [args.hermes_bin, "--profile", args.profile, "mcp", "test", "prime-agent"],
        timeout=30,
    )
    session_tools = _read_session_tools(args.session_tools_json)
    omp_verdict = None
    if args.omp_probe is not None:
        sibling_python = Path(args.hermes_bin).with_name("python")
        omp_python = args.omp_python or (
            str(sibling_python) if sibling_python.is_file() else sys.executable
        )
        omp_env = os.environ.copy()
        local_bin = str(Path.home() / ".local" / "bin")
        omp_env["PATH"] = args.omp_runtime_path or (
            local_bin + os.pathsep + omp_env.get("PATH", "")
        )
        omp = _run(
            [omp_python, str(args.omp_probe), "--strict"], timeout=240,
            env=omp_env,
        )
        report = _parse_last_json(omp.stdout)
        omp_verdict = str((report or {}).get("verdict") or "PROBE_FAILED")

    result = classify(
        prime_returncode=prime.returncode,
        prime_output=prime.stdout + "\n" + prime.stderr,
        session_tools=session_tools,
        omp_verdict=omp_verdict,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["next_action"] == "NONE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
