#!/usr/bin/env python3
"""Simple global and per-harness controls for optional orchestration systems."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prime_bridge.harness_registry import (
    UNIVERSAL_HARNESS_REGISTRY as HARNESS_REGISTRY,
    HarnessRegistryError,
    resolve_universal_targets as resolve_targets,
)
from prime_bridge.job_ledger import JobLedger
from prime_bridge.orca_control import OrcaControl
from prime_bridge.parallel_provider import ParallelProviderCatalog, ParallelProviderError
from prime_bridge.orchestration_switch import (
    FEATURES, OrchestrationSwitch, SwitchControlError, default_state_path,
)


def _ledger_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    override = os.environ.get("LAPART_PRIME_LEDGER")
    hermes_ledger = os.environ.get("HERMES_PRIME_LEDGER")
    candidates = [
        Path(override) if override else None,
        Path(hermes_ledger) if hermes_ledger else None,
        Path.home() / ".hermes" / "state" / "prime" / "jobs.sqlite3",
        ROOT.parent / ".hermes" / "state" / "prime" / "jobs.sqlite3",
    ]
    return next((path for path in candidates if path is not None and path.is_file()), None)


def _orca_provider(args, owner):
    ledger_path = _ledger_path(args.ledger)
    if ledger_path is None or not ledger_path.is_file():
        return None
    roots = tuple(args.allowed_root or (ROOT.parent,))
    ledger = JobLedger(ledger_path, roots)
    catalog = ParallelProviderCatalog.load(args.provider_catalog)
    service = catalog.build_service(
        "orca", ledger=ledger, allowed_roots=roots, root=ROOT,
        switch_path=owner.path,
    )
    return service.provider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path,
                        default=default_state_path().parent)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--allowed-root", type=Path, action="append")
    parser.add_argument(
        "--provider-catalog", type=Path,
        default=ROOT / "config" / "parallel-worker-providers.json",
    )
    parser.add_argument("command", choices=("status", "all", *FEATURES))
    parser.add_argument("mode", nargs="?")
    parser.add_argument("--feature", choices=FEATURES)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--global", dest="global_selector", action="store_true")
    selector.add_argument("--harness", action="append", choices=HARNESS_REGISTRY)
    args = parser.parse_args(argv)
    try:
        owner = OrchestrationSwitch(args.state_root)
        provider = None
        catalog = ParallelProviderCatalog.load(args.provider_catalog)
        try:
            provider = _orca_provider(args, owner)
        except ParallelProviderError:
            provider = None
        orca = OrcaControl(
            owner, provider=provider,
            provider_registered="orca" in catalog.specs,
        )
        if args.command == "status":
            if args.mode is not None:
                raise SwitchControlError("status does not accept a mode")
            targets = (
                resolve_targets(global_selector=args.global_selector,
                                harnesses=tuple(args.harness or ()))
                if args.global_selector or args.harness else HARNESS_REGISTRY
            )
            result = (
                orca.status(targets) if args.feature == "orca"
                else owner.status(feature=args.feature, harnesses=targets)
            )
        else:
            if not args.mode or args.feature:
                raise SwitchControlError("a mode and exactly one selector are required")
            targets = resolve_targets(
                global_selector=args.global_selector,
                harnesses=tuple(args.harness or ()))
            if args.command == "orca":
                result = orca.set(args.mode, targets)
            elif args.command == "all" and args.mode == "off":
                drained = orca.set("off", targets)
                if drained.get("result") in {"DRAIN_REQUIRED", "DRAIN_INCOMPLETE", "QUIESCE_FAILED"}:
                    result = drained
                else:
                    result = owner.set(
                        "all", "off", targets, verified_orca_off=True,
                    )
                    result["orca_drain"] = drained.get("drain")
            else:
                result = owner.set(args.command, args.mode, targets)
    except (HarnessRegistryError, ParallelProviderError, SwitchControlError,
            OSError, ValueError) as exc:
        result = {
            "command": args.command, "changed": False,
            "result": "INVALID", "reason": type(exc).__name__,
        }
    outcome = result.get("result", result.get("global", "INVALID"))
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"orchestration-control {args.command}: {outcome}")
    return 0 if outcome in {"ACTIVE", "INACTIVE", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
