#!/usr/bin/env python3
"""Render the managed Prime MCP overlay for one or more Hermes profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prime_bridge.profile_policy import PrimeProfilePolicy, render_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles-root", type=Path, required=True)
    parser.add_argument("--profile", action="append")
    parser.add_argument("--policy", type=Path, default=ROOT / "config/prime-profile-policy.json")
    parser.add_argument("--ledger", type=Path, default=Path("/home/__LINUX_USER__/.hermes/state/prime/jobs.sqlite3"))
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()
    PrimeProfilePolicy.load(args.policy)
    profiles = args.profile or sorted(
        path.name for path in args.profiles_root.iterdir()
        if path.is_dir() and (path / "config.yaml").is_file()
    )
    receipts = []
    for profile in profiles:
        source = args.profiles_root / profile / "config.yaml"
        if not source.is_file():
            raise SystemExit(f"missing profile config: {profile}")
        destination = source if args.in_place else args.profiles_root / profile / "config.prime-next.yaml"
        receipt_path = args.profiles_root / profile / "prime-overlay-receipt.json"
        receipts.append(render_file(
            source, destination, profile=profile, policy_path=args.policy,
            ledger_path=args.ledger, receipt_path=receipt_path,
        ))
    print(json.dumps({"status": "ok", "profiles": receipts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
