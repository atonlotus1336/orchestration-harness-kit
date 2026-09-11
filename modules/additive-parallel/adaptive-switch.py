#!/usr/bin/env python3
"""Reference CLI grammar for recipient-owned adaptive switch adapters."""
import argparse
import json

from adaptive_switch import resolve_targets


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("on", "off", "refresh"):
        command = sub.add_parser(name)
        selector = command.add_mutually_exclusive_group(required=True)
        selector.add_argument("--global", dest="global_selector", action="store_true")
        selector.add_argument("--harness", action="append")
    status = sub.add_parser("status")
    selector = status.add_mutually_exclusive_group()
    selector.add_argument("--global", dest="global_selector", action="store_true")
    selector.add_argument("--harness", action="append")
    args = parser.parse_args()
    registry = ("codex", "hermes")
    targets = registry if args.command == "status" and not (args.global_selector or args.harness) else resolve_targets(
        registry, global_selector=args.global_selector, harnesses=tuple(args.harness or ()))
    print(json.dumps({"command": args.command, "targets": targets,
                      "status": "ADAPTER_REQUIRED", "activation_changes": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
