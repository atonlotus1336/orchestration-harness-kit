#!/usr/bin/env python3
"""Manage a recipient-local token-compute activation registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


MODULE = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE))
from lapart_token_compute import (  # noqa: E402
    HARNESS_IDS,
    ActivationError,
    activate,
    load_policy,
    load_registry,
    rollback,
)


def _emit(status: str, command: str, **values) -> int:
    print(json.dumps({"status": status, "command": command, **values}, indent=2, sort_keys=True))
    return 0 if status in {"PASS", "DRY-RUN"} else 2


def _selected(values: list[str]) -> tuple[str, ...]:
    return HARNESS_IDS if "all" in values else tuple(dict.fromkeys(values))


def command_activate(args) -> int:
    selected = _selected(args.harness)
    if not args.apply:
        return _emit(
            "DRY-RUN",
            "activate",
            harnesses=list(selected),
            provider_calls=0,
            service_restarts=0,
            marginal_value_stop="shadow",
        )
    if not args.yes:
        return _emit("BLOCKED", "activate", reason="explicit --yes is required")
    value, snapshot = activate(
        args.registry,
        policy_path=args.policy,
        harnesses=selected,
        approval_reference=args.approval_reference,
    )
    return _emit(
        "PASS",
        "activate",
        harnesses=list(value["targets"]),
        provider_calls=0,
        service_restarts=0,
        rollback_snapshot=snapshot,
    )


def command_verify(args) -> int:
    _, policy_sha256 = load_policy(args.policy)
    value = load_registry(args.registry, policy_sha256=policy_sha256, require_all=args.require_all_active)
    return _emit(
        "PASS",
        "verify",
        harnesses=list(value["targets"]),
        provider_calls=value["provider_calls"],
        marginal_value_stop=value["controls"]["marginal_value_stop"],
    )


def command_rollback(args) -> int:
    if not (args.apply and args.yes):
        return _emit("DRY-RUN", "rollback", snapshot=args.snapshot)
    value = rollback(args.registry, args.snapshot)
    return _emit("PASS", "rollback", harnesses=list(value.get("targets", {})), provider_calls=0)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--policy", type=Path, default=MODULE / "token-compute-policy.json")
    commands = root.add_subparsers(dest="command", required=True)

    activate_command = commands.add_parser("activate")
    activate_command.add_argument("--registry", type=Path, required=True)
    activate_command.add_argument("--harness", action="append", choices=[*HARNESS_IDS, "all"], required=True)
    activate_command.add_argument("--approval-reference", required=True)
    activate_command.add_argument("--apply", action="store_true")
    activate_command.add_argument("--yes", action="store_true")
    activate_command.set_defaults(func=command_activate)

    verify_command = commands.add_parser("verify")
    verify_command.add_argument("--registry", type=Path, required=True)
    verify_command.add_argument("--require-all-active", action="store_true")
    verify_command.set_defaults(func=command_verify)

    rollback_command = commands.add_parser("rollback")
    rollback_command.add_argument("--registry", type=Path, required=True)
    rollback_command.add_argument("--snapshot", required=True)
    rollback_command.add_argument("--apply", action="store_true")
    rollback_command.add_argument("--yes", action="store_true")
    rollback_command.set_defaults(func=command_rollback)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except ActivationError as exc:
        return _emit("BLOCKED", args.command, reason=str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
