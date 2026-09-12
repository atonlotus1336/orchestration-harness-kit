#!/usr/bin/env python3
"""Toggle portable observation-only Orca state; recipient consumer required."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_switch import PortableSwitchError, resolve_targets
from orca_observer_switch import PortableOrcaObserverSwitch

REGISTRY = ("codex", "hermes")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("on", "off"):
        command = sub.add_parser(name)
        selector = command.add_mutually_exclusive_group(required=True)
        selector.add_argument("--global", dest="global_selector", action="store_true")
        selector.add_argument("--harness", action="append", choices=REGISTRY)
    status = sub.add_parser("status")
    selector = status.add_mutually_exclusive_group()
    selector.add_argument("--global", dest="global_selector", action="store_true")
    selector.add_argument("--harness", action="append", choices=REGISTRY)
    args = parser.parse_args(argv)
    try:
        targets = (REGISTRY if args.command == "status"
                   and not (args.global_selector or args.harness)
                   else resolve_targets(
                       REGISTRY, global_selector=args.global_selector,
                       harnesses=tuple(args.harness or ())))
        owner = PortableOrcaObserverSwitch(args.state_root, REGISTRY)
        result = (owner.status() if args.command == "status"
                  else owner.set(targets, turn_on=args.command == "on"))
        outcome = result.get("result", result.get("global", "INVALID"))
        payload = {"command": args.command, "status": outcome,
                   "targets": list(targets), "result": result,
                   "activation_changes": len(result.get("changed_harnesses", []))}
    except Exception as exc:
        payload = {"command": args.command, "status": "INVALID",
                   "activation_changes": 0, "reason": type(exc).__name__}
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] in {"ACTIVE", "INACTIVE", "PARTIAL"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
