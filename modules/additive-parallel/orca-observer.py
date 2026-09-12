#!/usr/bin/env python3
"""Compatibility CLI for the portable unified Orca control."""
import argparse
import json
from pathlib import Path

from adaptive_switch import PortableSwitchError, resolve_targets
from orca_observer_switch import HARNESS_REGISTRY, PortableOrcaObserverSwitch


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("on", "off"):
        command = sub.add_parser(name)
        selector = command.add_mutually_exclusive_group(required=True)
        selector.add_argument("--global", dest="global_selector", action="store_true")
        selector.add_argument("--harness", action="append", choices=HARNESS_REGISTRY)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        owner = PortableOrcaObserverSwitch(args.state_root)
        if args.command == "status":
            result = owner.status()
        else:
            targets = resolve_targets(
                HARNESS_REGISTRY, global_selector=args.global_selector,
                harnesses=tuple(args.harness or ()),
            )
            result = owner.set(targets, turn_on=args.command == "on")
    except Exception as exc:
        result = {"result": "INVALID", "reason": type(exc).__name__}
    print(json.dumps(result, sort_keys=True))
    outcome = result.get("result", result.get("global", "INVALID"))
    return 0 if outcome in {"ACTIVE", "INACTIVE", "PARTIAL"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
