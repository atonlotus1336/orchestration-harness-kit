#!/usr/bin/env python3
"""Operate recipient-owned adaptive switch adapters."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from adaptive_switch import PortableSwitch, PortableSwitchError, resolve_targets

DEFAULT_REGISTRY = ("codex", "hermes")
SUCCESS_STATES = {"ACTIVE", "INACTIVE", "PARTIAL"}


def _load_adapter_module(path):
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise PortableSwitchError("adapter module must be a regular file")
    path = raw_path.resolve()
    if not path.is_file():
        raise PortableSwitchError("adapter module must be a regular file")
    spec = importlib.util.spec_from_file_location(
        "recipient_adaptive_switch_adapters", path)
    if not spec or not spec.loader:
        raise PortableSwitchError("adapter module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = tuple(getattr(module, "HARNESS_REGISTRY", ()))
    factory = getattr(module, "build_adapters", None)
    if not registry or not callable(factory):
        raise PortableSwitchError("adapter module contract is incomplete")
    return registry, factory


def _emit(value):
    print(json.dumps(value, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--adapter-module", type=Path)
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

    registry = DEFAULT_REGISTRY
    if args.adapter_module is None or args.state_root is None:
        targets = registry if args.command == "status" and not (
            args.global_selector or args.harness) else resolve_targets(
                registry, global_selector=args.global_selector,
                harnesses=tuple(args.harness or ()))
        _emit({"activation_changes": 0, "command": args.command,
               "status": "ADAPTER_REQUIRED", "targets": list(targets)})
        return 3

    try:
        registry, factory = _load_adapter_module(args.adapter_module)
        targets = registry if args.command == "status" and not (
            args.global_selector or args.harness) else resolve_targets(
                registry, global_selector=args.global_selector,
                harnesses=tuple(args.harness or ()))
        owner = PortableSwitch(
            args.state_root, registry, factory(Path(args.state_root)))
        if args.command == "status":
            state = owner.status()
            result = {"changed": False, "changed_harnesses": [],
                      "result": state["global"], "after": state}
        elif args.command == "refresh":
            result = owner.refresh(targets)
        else:
            result = owner.set(targets, turn_on=args.command == "on")
    except Exception as exc:
        _emit({"activation_changes": 0, "command": args.command,
               "status": "ADAPTER_INVALID", "reason": type(exc).__name__})
        return 4

    payload = {
        "activation_changes": len(result.get("changed_harnesses", [])),
        "command": args.command,
        "status": result.get("result", "INVALID"),
        "targets": list(targets),
        "result": result,
    }
    _emit(payload)
    return 0 if payload["status"] in SUCCESS_STATES else 5


if __name__ == "__main__":
    raise SystemExit(main())
