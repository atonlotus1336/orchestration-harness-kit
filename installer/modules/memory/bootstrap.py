#!/usr/bin/env python3
"""Create an empty recipient-owned MemoryWeaver data directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = args.data_dir.expanduser().resolve()
    if target == Path(target.anchor) or len(target.parts) < 2:
        print(json.dumps({"status": "BLOCKED", "reason": "unsafe data directory"}))
        return 2
    if args.apply:
        for name in ("chroma", "spool", "spool/done", "spool/error"):
            (target / name).mkdir(parents=True, exist_ok=True)
    print(json.dumps({"status": "PASS" if args.apply else "DRY-RUN", "data_dir": "<recipient-data-dir>"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
