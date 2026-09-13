#!/usr/bin/env python3
"""Provider-free Herdr runtime/schema probe.

This script never installs, activates, or starts reviewer sessions. It only
checks an explicitly selected local executable against the inactive lock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "config" / "herdr-runtime-lock.json"
DEFAULT_WINDOWS = ROOT.parent / "herdr" / "herdr.cmd"
DEFAULT_WINDOWS_DIRECT = ROOT.parent / "herdr" / ".lapart-herdr" / "herdr.exe"


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()


def _method_names(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"method", "name", "id"} and isinstance(child, str):
                found.add(child)
            found.update(_method_names(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_method_names(child))
    elif isinstance(value, str):
        found.add(value)
    return found


def _command(executable: Path, *args: str) -> list[str]:
    if os.name == "nt" and executable.suffix.lower() == ".cmd":
        return ["cmd.exe", "/d", "/s", "/c", str(executable), *args]
    return [str(executable), *args]


def _run(runner: Callable[..., subprocess.CompletedProcess[str]], command: Sequence[str], timeout: float):
    return runner(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="strict", timeout=timeout, check=False,
    )


def probe(lock_path: Path = DEFAULT_LOCK, executable: Path | None = None, *,
          timeout: float = 5.0, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> dict[str, Any]:
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {"status": "UNAVAILABLE", "runnable": False, "reason": "LOCK_UNREADABLE"}

    candidate = executable
    if candidate is None:
        for path in (DEFAULT_WINDOWS, DEFAULT_WINDOWS_DIRECT):
            if path.is_file():
                candidate = path
                break
    if candidate is None or not candidate.is_file():
        return {"status": "UNAVAILABLE", "runnable": False, "reason": "EXECUTABLE_NOT_FOUND"}

    try:
        version = _run(runner, _command(candidate, "--version"), timeout)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "UNAVAILABLE", "runnable": False, "reason": "VERSION_PROBE_FAILED"}
    observed_version = (version.stdout or "").strip()
    if version.returncode != 0 or observed_version != lock.get("expected_version_output"):
        return {"status": "UNAVAILABLE", "runnable": False, "reason": "VERSION_MISMATCH"}

    try:
        schema_result = _run(runner, _command(candidate, "api", "schema", "--json"), timeout)
        schema = json.loads(schema_result.stdout)
    except subprocess.TimeoutExpired:
        return {"status": "UNAVAILABLE", "runnable": False, "reason": "SCHEMA_PROBE_TIMEOUT"}
    except (OSError, UnicodeError, ValueError):
        return {"status": "UNAVAILABLE", "runnable": False, "reason": "SCHEMA_UNREADABLE"}
    if schema_result.returncode != 0 or not isinstance(schema, Mapping):
        return {"status": "UNAVAILABLE", "runnable": False, "reason": "SCHEMA_UNREADABLE"}

    observed_digest = _canonical_sha256(schema)
    expected_digest = lock.get("schema_sha256")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
        return {
            "status": "DEGRADED", "runnable": False,
            "reason": "SCHEMA_DIGEST_UNPINNED", "version": observed_version,
            "schema_sha256": observed_digest,
        }
    if observed_digest != expected_digest:
        return {"status": "UNAVAILABLE", "runnable": False, "reason": "SCHEMA_DIGEST_MISMATCH"}

    missing = sorted(set(lock.get("required_methods", ())) - _method_names(schema))
    if missing:
        return {
            "status": "UNAVAILABLE", "runnable": False,
            "reason": "REQUIRED_METHODS_MISSING", "missing_method_count": len(missing),
        }
    return {
        "status": "READY", "runnable": lock.get("activation_state") != "inactive" and lock.get("runnable") is True,
        "reason": "QUALIFIED_INACTIVE" if lock.get("activation_state") == "inactive" else "QUALIFIED",
        "version": observed_version, "schema_sha256": observed_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    result = probe(args.lock, args.executable, timeout=args.timeout)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"READY", "DEGRADED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
