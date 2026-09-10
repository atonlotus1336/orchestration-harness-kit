#!/usr/bin/env python3
"""Initialize gitignored maintainer privacy policies without printing identities."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import socket
import subprocess
from pathlib import Path


def _remote_owner(repository: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=False,
    )
    match = re.search(r"(?:github\.com[:/])([^/]+)/", result.stdout)
    return match.group(1) if match else ""


def identity_values(repository: Path) -> list[str]:
    """Return local identity markers; callers must never print the values."""
    candidates = {
        getpass.getuser(), socket.gethostname(), Path.home().name,
        os.environ.get("USERNAME", ""), os.environ.get("USER", ""),
        os.environ.get("COMPUTERNAME", ""), _remote_owner(repository),
    }
    return sorted(
        {value.strip() for value in candidates if len(value.strip()) >= 4},
        key=str.casefold,
    )


def policy_payload(values: list[str]) -> tuple[str, str]:
    denylist = "# Generated on this machine. Values are regex-escaped identity markers.\n"
    denylist += "\n".join(re.escape(value) for value in values) + "\n"
    rules = [
        {"From": value, "To": "alice" if index else "devbox"}
        for index, value in enumerate(values)
    ]
    return denylist, json.dumps(rules, indent=2) + "\n"


def selftest() -> int:
    denylist, rules = policy_payload(["ExampleUser", "EXAMPLE-HOST"])
    parsed = json.loads(rules)
    passed = (
        "ExampleUser" in denylist
        and len(parsed) == 2
        and all(item["To"] in {"alice", "devbox"} for item in parsed)
    )
    print(json.dumps({"cases": 3, "failures": 0 if passed else 1}, sort_keys=True))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    repository = args.root.resolve()
    values = identity_values(repository)
    if not values:
        print(json.dumps({"status": "BLOCKED", "reason": "no_identity_markers"}))
        return 2
    targets = [repository / ".private-denylist", repository / ".sanitize-rules.local.json"]
    if any(path.exists() for path in targets) and not args.force:
        print(json.dumps({"status": "BLOCKED", "reason": "policy_already_exists"}))
        return 2
    if not args.apply:
        print(json.dumps({"status": "DRY-RUN", "identity_marker_count": len(values)}))
        return 0

    denylist, rules = policy_payload(values)
    targets[0].write_text(denylist, encoding="utf-8")
    targets[1].write_text(rules, encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "identity_marker_count": len(values),
        "files_written": len(targets),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
