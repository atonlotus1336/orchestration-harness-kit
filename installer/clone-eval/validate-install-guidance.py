#!/usr/bin/env python3
"""Accept only the exact command named by one small-model state card."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def expected_from_card(path: Path) -> str:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[-1]


def validate(card: Path, response: str) -> dict:
    expected = expected_from_card(card)
    candidates = [line.strip() for line in response.splitlines() if line.strip() == expected]
    altered_commands = [
        line.strip() for line in response.splitlines()
        if line.strip().startswith(("python", "STOP:")) and line.strip() != expected
    ]
    passed = len(candidates) == 1 and not altered_commands
    return {"status": "PASS" if passed else "BLOCKED", "exact_matches": len(candidates), "altered_commands": len(altered_commands)}


def selftest(root: Path) -> int:
    card = root / "install-guidance-after-install-pass.txt"
    expected = expected_from_card(card)
    cases = [
        (expected, "PASS"),
        ("Here is the requested output:\n\n" + expected, "PASS"),
        (expected + " --apply", "BLOCKED"),
        (expected + "\npython3 -m pip install unknown", "BLOCKED"),
    ]
    for response, status in cases:
        if validate(card, response)["status"] != status:
            return 2
    print(json.dumps({"status": "PASS", "cases": len(cases)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--card", type=Path)
    parser.add_argument("--response", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.selftest:
        return selftest(root)
    if not args.card:
        parser.error("--card is required")
    response = args.response.read_text(encoding="utf-8") if args.response else sys.stdin.read()
    result = validate(args.card, response)
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
