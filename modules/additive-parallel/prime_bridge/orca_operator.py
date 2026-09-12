#!/usr/bin/env python3
"""Closed-input operator surface for the governed Orca execution provider."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prime_bridge.harness_registry import UNIVERSAL_HARNESS_REGISTRY
from prime_bridge.job_ledger import JobLedger, validate_safe
from prime_bridge.orca_execution import (
    OrcaCLI,
    OrcaExecutionError,
    OrcaExecutionProvider,
    OrcaHarnessRegistry,
)
from prime_bridge.orchestration_switch import default_state_path
from prime_bridge.worker_transport import WorkerTransport


OPERATIONS = (
    "status", "probe", "start", "show", "read", "send", "check",
    "stop", "abandon", "release", "retain", "retry", "task-create",
    "task-list", "task-update", "reply", "inbox", "worker-list",
    "gate-create", "gate-resolve", "gate-list",
)


def _input() -> dict:
    raw = sys.stdin.buffer.read(65_537)
    if len(raw) > 65_536:
        raise OrcaExecutionError("Orca operator input exceeds 64 KiB")
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OrcaExecutionError("Orca operator input is invalid JSON") from exc
    if not isinstance(value, dict):
        raise OrcaExecutionError("Orca operator input must be an object")
    validate_safe(value, max_bytes=65_536)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--allowed-root", type=Path, action="append", required=True)
    parser.add_argument(
        "--registry", type=Path,
        default=ROOT / "config" / "orca-harness-adapters.json",
    )
    parser.add_argument("--switch-state", type=Path, default=default_state_path())
    parser.add_argument("--harness", choices=UNIVERSAL_HARNESS_REGISTRY)
    parser.add_argument("--orca", default="orca")
    return parser


def _call(provider: OrcaExecutionProvider, operation: str,
          harness: str | None, value: dict) -> object:
    if operation == "status":
        return provider.status((harness,) if harness else None)
    if operation == "probe":
        if harness is None:
            return {name: provider.probe(name) for name in UNIVERSAL_HARNESS_REGISTRY}
        return provider.probe(harness)
    if operation == "start":
        return provider.start(context=value.pop("context"), **value)
    if operation == "retry":
        return provider.retry(value.pop("binding_id"), context=value.pop("context"), **value)
    binding_id = value.pop("binding_id")
    method = getattr(provider, operation.replace("-", "_"))
    return method(binding_id, **value)


def main() -> int:
    args = _parser().parse_args()
    try:
        registry = OrcaHarnessRegistry.load(args.registry)
        ledger = JobLedger(args.ledger, args.allowed_root)
        transport = WorkerTransport(
            ledger, orca_harnesses=UNIVERSAL_HARNESS_REGISTRY,
        )
        provider = OrcaExecutionProvider(
            ledger=ledger, transport=transport, registry=registry,
            client=OrcaCLI(args.orca, cwd=ROOT), switch_path=args.switch_state,
        )
        result = _call(provider, args.operation, args.harness, _input())
        print(json.dumps({"ok": True, "result": result}, sort_keys=True))
        return 0
    except (KeyError, OSError, TypeError, ValueError, OrcaExecutionError) as exc:
        print(json.dumps({
            "ok": False, "error": type(exc).__name__, "message": str(exc),
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
