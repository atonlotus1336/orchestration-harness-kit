#!/usr/bin/env python3
"""Inspect Prime/OMP structurally by default; run live probes only with --deep."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PRIME_TOOL_PREFIXES = ("prime_", "mcp_prime_")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def structural_status(
    config_path: Path | None, *, verify_binaries: bool = False,
) -> dict[str, Any]:
    """Inspect staged configuration without contacting a model or MCP server."""
    if config_path is None or not config_path.is_file():
        return {
            "schema_version": 1,
            "status": "BLOCKED",
            "live_probe": False,
            "components": {"prime": "MISSING_CONFIG", "omp": "MISSING_CONFIG"},
            "next_action": "PREPARE_DISABLED_CONFIG",
        }
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": 1,
            "status": "BLOCKED",
            "live_probe": False,
            "components": {"prime": "INVALID_CONFIG", "omp": "INVALID_CONFIG"},
            "next_action": "PREPARE_DISABLED_CONFIG",
        }

    components: dict[str, str] = {}
    for name in ("prime", "omp"):
        record = config.get(name, {})
        executable = Path(str(record.get("executable", "")))
        expected = str(record.get("sha256", "")).lower()
        if not executable.is_file():
            components[name] = "MISSING_EXECUTABLE"
        elif len(expected) != 64 or any(
            character not in "0123456789abcdef" for character in expected
        ):
            components[name] = "UNPINNED"
        elif verify_binaries and _sha256(executable) != expected:
            components[name] = "DRIFTED"
        else:
            components[name] = "PINNED"

    ready = all(value == "PINNED" for value in components.values())
    enabled = config.get("enabled") is True
    if not ready:
        status, action = "BLOCKED", "REPAIR_LOCAL_INSTALLATION"
    elif enabled:
        status, action = "ACTIVE", "RUN_DEEP_DIAGNOSTIC"
    else:
        status, action = "CONFIGURED_INACTIVE", "REVIEW_AND_ACTIVATE"
    return {
        "schema_version": 1,
        "status": status,
        "live_probe": False,
        "binary_hash_verified": bool(verify_binaries and ready),
        "components": components,
        "next_action": action,
    }


def classify(
    *, prime_returncode: int, prime_output: str,
    session_tools: list[str] | None, omp_verdict: str | None,
) -> dict[str, Any]:
    """Create a typed diagnosis from independently observed live boundaries."""
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
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        prime, omp = root / "prime", root / "omp"
        prime.write_bytes(b"prime")
        omp.write_bytes(b"omp")
        config = root / "config.json"
        config.write_text(json.dumps({
            "enabled": False,
            "prime": {"executable": str(prime), "sha256": _sha256(prime)},
            "omp": {"executable": str(omp), "sha256": _sha256(omp)},
        }), encoding="utf-8")
        cases["quick-status-provider-free"] = (
            structural_status(config)["status"] == "CONFIGURED_INACTIVE"
        )
        omp.write_bytes(b"changed")
        cases["integrity-drift-blocks"] = (
            structural_status(config, verify_binaries=True)["components"]["omp"]
            == "DRIFTED"
        )
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
    parser.add_argument("--config", type=Path, help="configuration from prepare-prime-omp.py")
    parser.add_argument("--deep", action="store_true", help="run live Prime and optional OMP probes")
    parser.add_argument(
        "--verify-binaries", action="store_true",
        help="hash configured binaries without making provider calls",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    # Supplying a live-only option preserves the earlier invocation contract.
    deep = args.deep or args.omp_probe is not None or args.session_tools_json is not None
    if not deep:
        result = structural_status(args.config, verify_binaries=args.verify_binaries)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] in {"ACTIVE", "CONFIGURED_INACTIVE"} else 2

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
    result["live_probe"] = True
    print(json.dumps(result, sort_keys=True))
    return 0 if result["next_action"] == "NONE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
