#!/usr/bin/env python3
"""Plan or execute the exact central Prime emergency-disable transaction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prime_bridge.emergency_control import EmergencyController  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-before-sha256")
    parser.add_argument("--rollback-id")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    controller = EmergencyController(args.policy, args.backup_root)
    if not args.apply:
        value = dict(controller.plan())
        value.pop("candidate")
    else:
        if not args.expected_before_sha256 or not args.rollback_id:
            parser.error("--apply requires --expected-before-sha256 and --rollback-id")
        value = controller.apply(
            expected_before_sha256=args.expected_before_sha256,
            rollback_id=args.rollback_id, confirmation=args.confirm or "",
        )
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
