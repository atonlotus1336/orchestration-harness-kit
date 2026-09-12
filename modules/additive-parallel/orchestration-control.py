#!/usr/bin/env python3
"""Operate the portable unified orchestration switch contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_switch import PortableSwitchError, resolve_targets
from orchestration_switch import (
    FEATURES, HARNESS_REGISTRY, PortableOrchestrationSwitch,
)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("command", choices=("status", "all", *FEATURES))
    parser.add_argument("mode", nargs="?")
    parser.add_argument("--feature", choices=FEATURES)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--global", dest="global_selector", action="store_true")
    selector.add_argument("--harness", action="append", choices=HARNESS_REGISTRY)
    args = parser.parse_args(argv)
    try:
        owner = PortableOrchestrationSwitch(args.state_root)
        if args.command == "status":
            targets = (
                resolve_targets(
                    HARNESS_REGISTRY, global_selector=args.global_selector,
                    harnesses=tuple(args.harness or ()))
                if args.global_selector or args.harness else HARNESS_REGISTRY)
            result = owner.status(args.feature, targets)
        else:
            if not args.mode:
                raise PortableSwitchError("mode is required")
            targets = resolve_targets(
                HARNESS_REGISTRY, global_selector=args.global_selector,
                harnesses=tuple(args.harness or ()))
            result = owner.set(args.command, args.mode, targets)
    except Exception as exc:
        result = {"result": "INVALID", "changed": False,
                  "reason": type(exc).__name__}
    print(json.dumps(result, sort_keys=True))
    outcome = result.get("result", result.get("global", "INVALID"))
    return 0 if outcome in {"ACTIVE", "INACTIVE", "PARTIAL"} else 3


if __name__ == "__main__":
    raise SystemExit(main())

