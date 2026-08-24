#!/usr/bin/env python3
"""Verify a fixed, key-only SSH identity probe without exposing key material."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


SAFE_DESTINATION = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")


def build_command(args: argparse.Namespace) -> list[str]:
    command = [
        args.ssh,
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "StrictHostKeyChecking=yes",
        "-i", str(args.identity_file),
    ]
    if args.known_hosts:
        command.extend(["-o", f"UserKnownHostsFile={args.known_hosts}"])
    command.append(args.destination)
    if args.runtime_user:
        command.append(
            f"hostname; wsl.exe -d {args.wsl_distribution} -u {args.runtime_user} -- id -un"
        )
    else:
        command.append("hostname; id -un")
    return command


def validate_result(
    *, return_code: int, stdout: str, expected_host: str, expected_user: str
) -> list[str]:
    if return_code != 0:
        return ["SSH identity probe failed; verify the approved public key, host key, and server policy."]
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 2:
        return ["SSH identity probe returned an unexpected transcript shape."]
    if not re.fullmatch(expected_host, lines[0]):
        return ["Remote hostname did not match the approved expectation."]
    if lines[1] != expected_user:
        return ["Remote runtime user did not match the approved expectation."]
    return []


def selftest() -> int:
    cases = [
        (0, "example-host\nalice\n", r"example-host", "alice", False),
        (255, "", r"example-host", "alice", True),
        (0, "wrong-host\nalice\n", r"example-host", "alice", True),
        (0, "example-host\nbob\n", r"example-host", "alice", True),
        (0, "example-host\nalice\nextra\n", r"example-host", "alice", True),
    ]
    failures = sum(
        bool(validate_result(return_code=code, stdout=output, expected_host=host, expected_user=user)) != should_fail
        for code, output, host, user, should_fail in cases
    )
    print(json.dumps({"cases": len(cases), "failures": failures}, sort_keys=True))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--ssh", default="ssh")
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument("--destination")
    parser.add_argument("--expected-host")
    parser.add_argument("--expected-user")
    parser.add_argument("--runtime-user")
    parser.add_argument("--wsl-distribution")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    required = (args.identity_file, args.destination, args.expected_host, args.expected_user)
    if not all(required):
        parser.error("--identity-file, --destination, --expected-host, and --expected-user are required")
    if not SAFE_DESTINATION.fullmatch(args.destination):
        parser.error("--destination must be user@host with no shell metacharacters")
    if not SAFE_VALUE.fullmatch(args.expected_user):
        parser.error("--expected-user contains unsupported characters")
    if bool(args.runtime_user) != bool(args.wsl_distribution):
        parser.error("--runtime-user and --wsl-distribution must be supplied together")
    if args.runtime_user and (not SAFE_VALUE.fullmatch(args.runtime_user) or not SAFE_VALUE.fullmatch(args.wsl_distribution)):
        parser.error("WSL distribution and runtime user contain unsupported characters")
    if not args.identity_file.is_file():
        print(json.dumps({"status": "RED", "errors": ["identity file is absent"]}, sort_keys=True))
        return 2
    try:
        result = subprocess.run(build_command(args), capture_output=True, text=True, timeout=args.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "RED", "errors": [f"SSH identity probe could not run: {type(exc).__name__}"]}, sort_keys=True))
        return 2
    errors = validate_result(
        return_code=result.returncode,
        stdout=result.stdout,
        expected_host=args.expected_host,
        expected_user=args.runtime_user or args.expected_user,
    )
    print(json.dumps({"status": "GREEN" if not errors else "RED", "errors": errors}, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
