#!/usr/bin/env python3
"""Simple global and per-harness switches for Orca observation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prime_bridge.harness_registry import (
    UNIVERSAL_HARNESS_REGISTRY as HARNESS_REGISTRY,
    HarnessRegistryError,
    resolve_universal_targets as resolve_targets,
)
from prime_bridge.orca_observer_switch import (
    OrcaObserverSwitch, OrcaSwitchError, default_state_path,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path,
                        default=default_state_path().parent)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("on", "off"):
        command = sub.add_parser(name)
        selector = command.add_mutually_exclusive_group(required=True)
        selector.add_argument("--global", dest="global_selector", action="store_true")
        selector.add_argument("--harness", action="append",
                              choices=HARNESS_REGISTRY)
    status = sub.add_parser("status")
    selector = status.add_mutually_exclusive_group()
    selector.add_argument("--global", dest="global_selector", action="store_true")
    selector.add_argument("--harness", action="append", choices=HARNESS_REGISTRY)
    args = parser.parse_args(argv)
    try:
        targets = (HARNESS_REGISTRY if args.command == "status"
                   and not (args.global_selector or args.harness)
                   else resolve_targets(global_selector=args.global_selector,
                                        harnesses=tuple(args.harness or ())))
        owner = OrcaObserverSwitch(args.state_root)
        result = (owner.status(targets) if args.command == "status"
                  else owner.set(targets, turn_on=args.command == "on"))
    except (HarnessRegistryError, OrcaSwitchError, OSError, ValueError) as exc:
        result = {"command": args.command, "result": "INVALID",
                  "changed": False, "reason": type(exc).__name__}
    outcome = result.get("result", result.get("global", "INVALID"))
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"orca-observer {args.command}: {outcome}")
    return 0 if outcome in {"ACTIVE", "INACTIVE", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
