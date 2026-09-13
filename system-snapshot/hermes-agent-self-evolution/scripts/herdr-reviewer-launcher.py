
#!/usr/bin/env python3
"""Fixed report-only reviewer launcher used inside an isolated Herdr pane."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_RESULT_BYTES = 1_048_576
ALLOWED_ENV = frozenset({
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG", "LC_ALL",
})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or args.request.is_symlink() or args.result.exists():
        return 2
    root = args.request.parent.resolve()
    if args.request.resolve().parent != root or args.result.resolve().parent != root:
        return 2
    request = args.request.read_bytes()
    env = {key: value for key, value in os.environ.items() if key in ALLOWED_ENV}
    try:
        result = subprocess.run(
            command, input=request, capture_output=True, cwd=root, env=env,
            timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        failure = root / "failure.json"
        failure.write_text(
            json.dumps({"error_type": type(exc).__name__, "output_accepted": False}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 3
    if result.returncode or len(result.stdout) > MAX_RESULT_BYTES:
        failure = root / "failure.json"
        failure.write_text(
            json.dumps({"returncode": result.returncode, "output_accepted": False}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 4
    try:
        value = json.loads(result.stdout)
    except (UnicodeError, ValueError):
        failure = root / "failure.json"
        failure.write_text(
            json.dumps({"error_type": "InvalidReviewerOutput", "output_accepted": False}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 5
    temporary = root / ".result.tmp"
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
