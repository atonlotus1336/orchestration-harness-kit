#!/usr/bin/env python3
"""Human-facing activation, deactivation, status, and diagnostics for Orca."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prime_bridge.harness_registry import (  # noqa: E402
    UNIVERSAL_HARNESS_REGISTRY,
)
from prime_bridge.job_ledger import JobLedger  # noqa: E402
from prime_bridge.orca_control import OrcaControl  # noqa: E402
from prime_bridge.orchestration_switch import (  # noqa: E402
    OrchestrationSwitch, default_state_path,
)
from prime_bridge.parallel_provider import ParallelProviderCatalog  # noqa: E402


PRIORITY_HARNESSES = (
    "codex", "hermes", "moa", "cursor", "dsh", "pi", "opencode",
)
DEFERRED_HARNESSES = ("claude-code", "antigravity")


@dataclass(frozen=True)
class ControlContext:
    control: OrcaControl
    state_root: Path
    ledger: Path | None
    workspace: Path


def _first_file(paths) -> Path | None:
    return next((path for path in paths if path is not None and path.is_file()), None)


def _ledger_path(explicit: Path | None, workspace: Path) -> Path | None:
    if explicit is not None:
        return explicit
    lapart = os.environ.get("LAPART_PRIME_LEDGER")
    hermes = os.environ.get("HERMES_PRIME_LEDGER")
    return _first_file((
        Path(lapart) if lapart else None,
        Path(hermes) if hermes else None,
        Path.home() / ".hermes" / "state" / "prime" / "jobs.sqlite3",
        workspace / ".hermes" / "state" / "prime" / "jobs.sqlite3",
    ))


def build_context(args) -> ControlContext:
    workspace = Path(args.workspace or ROOT.parent).resolve()
    state_root = Path(args.state_root or default_state_path().parent).resolve()
    ledger_path = _ledger_path(args.ledger, workspace)
    switch = OrchestrationSwitch(state_root)
    catalog = ParallelProviderCatalog.load(
        Path(args.provider_catalog or ROOT / "config/parallel-worker-providers.json")
    )
    provider = None
    if ledger_path is not None and ledger_path.is_file():
        ledger = JobLedger(ledger_path, (workspace,))
        provider = catalog.build_service(
            "orca", ledger=ledger, allowed_roots=(workspace,), root=ROOT,
            switch_path=switch.path,
        ).provider
    return ControlContext(
        control=OrcaControl(
            switch, provider=provider,
            provider_registered="orca" in catalog.specs,
        ),
        state_root=state_root, ledger=ledger_path, workspace=workspace,
    )


def _targets(args) -> tuple[str, ...]:
    if args.harness:
        requested = set(args.harness)
        return tuple(name for name in UNIVERSAL_HARNESS_REGISTRY if name in requested)
    if args.all_harnesses:
        return UNIVERSAL_HARNESS_REGISTRY
    return PRIORITY_HARNESSES


def _action(harness: str, desired: str, runtime: dict[str, Any],
            probe: dict[str, Any] | None, *, deferred: bool) -> str:
    if deferred:
        return "Deferred by rollout policy; no action required."
    if desired != "full":
        return f"python scripts/orca-control.py on --harness {harness}"
    if runtime.get("qualification") != "production":
        if probe and probe.get("reason") == "HARNESS_EXECUTABLE_MISSING":
            return (
                f"Install or enable the {harness} executable, then run: "
                f"python scripts/orca-control.py doctor --harness {harness}"
            )
        return (
            "Run governed A/B qualification: python "
            f"scripts/qualify-orca-coding.py --harness {harness} "
            "--native-baseline <verified-baseline.json> --promote-registry"
        )
    if probe and probe.get("reason") == "HARNESS_EXECUTABLE_MISSING":
        return (
            f"Install or enable the {harness} executable, then run: "
            f"python scripts/orca-control.py doctor --harness {harness}"
        )
    if probe and probe.get("status") != "READY":
        return f"Restore Orca runtime, then run: python scripts/orca-control.py doctor --harness {harness}"
    if runtime.get("runtime_ready") != "READY":
        return f"Run: python scripts/orca-control.py doctor --harness {harness}"
    return "Ready; no action required."


def readiness_report(context: ControlContext, targets: tuple[str, ...],
                     *, command: str, operation=None) -> dict[str, Any]:
    snapshot = context.control.status(targets)
    runtime = snapshot.get("runtime", {})
    runtime_rows = runtime.get("harnesses", {}) if isinstance(runtime, dict) else {}
    switch_rows = snapshot.get("features", {}).get("orca", {}).get("harnesses", {})
    rows = []
    active = 0
    considered = 0
    for harness in targets:
        desired = switch_rows.get(harness, "unknown")
        row = dict(runtime_rows.get(harness, {}))
        deferred = harness in DEFERRED_HARNESSES
        probe = None
        if (not deferred and desired == "full"
                and context.control.provider is not None):
            try:
                probe = dict(context.control.provider.probe(harness))
            except Exception as exc:
                probe = {"status": "BLOCKED", "reason": type(exc).__name__}
        ready = (
            not deferred and desired == "full"
            and row.get("qualification") == "production"
            and row.get("runtime_ready") == "READY"
            and probe is not None and probe.get("status") == "READY"
        )
        state = "DEFERRED" if deferred else "ACTIVE" if ready else "BLOCKED"
        if not deferred:
            considered += 1
            active += int(ready)
        rows.append({
            "harness": harness,
            "switch": desired,
            "runtime": (
                probe.get("status") if probe is not None
                else row.get("runtime_ready", runtime.get("runtime_ready", "NOT_CONNECTED"))
            ),
            "qualification": row.get("qualification", "unknown"),
            "state": state,
            "action": _action(harness, desired, row, probe, deferred=deferred),
        })
    overall = (
        "ACTIVE" if considered and active == considered
        else "PARTIAL" if active
        else "BLOCKED"
    )
    return {
        "command": command,
        "status": overall,
        "priority_harnesses": list(PRIORITY_HARNESSES),
        "deferred_harnesses": list(DEFERRED_HARNESSES),
        "rows": rows,
        "paths": {
            "state": str(context.state_root),
            "ledger": str(context.ledger) if context.ledger else "not found",
            "workspace": str(context.workspace),
        },
        "operation": operation,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"Orca {report['command']} readiness: {report['status']}")
    operation = report.get("operation")
    if operation:
        detail = f"Command result: {operation.get('result', 'UNKNOWN')}"
        drain = operation.get("drain")
        if drain:
            detail += (
                f"; drain={drain.get('status', 'UNKNOWN')}"
                f"; remaining={drain.get('remaining', 'UNKNOWN')}"
            )
        print(detail)
    print("Harness       Switch      Runtime       Qualification  State     Next action")
    for row in report["rows"]:
        print(
            f"{row['harness']:<13} {row['switch']:<11} {row['runtime']:<13} "
            f"{row['qualification']:<14} {row['state']:<9} {row['action']}"
        )
    print("Deferred: " + ", ".join(report["deferred_harnesses"]))


def main(argv=None, *, context_factory=build_context) -> int:
    parser = argparse.ArgumentParser(
        description="One safe front door for Orca activation and readiness.",
    )
    parser.add_argument("command", choices=("status", "on", "off", "doctor"))
    parser.add_argument("--harness", action="append", choices=UNIVERSAL_HARNESS_REGISTRY)
    parser.add_argument("--all-harnesses", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--state-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--ledger", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--provider-catalog", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.harness and args.all_harnesses:
        parser.error("--harness and --all-harnesses are mutually exclusive")
    targets = _targets(args)
    context = context_factory(args)
    operation = None
    if args.command == "on":
        enabled = tuple(name for name in targets if name not in DEFERRED_HARNESSES)
        operation = context.control.set("full", enabled) if enabled else {
            "changed": False, "result": "DEFERRED",
        }
    elif args.command == "off":
        operation = context.control.set("off", targets)
    report = readiness_report(
        context, targets, command=args.command, operation=operation,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    if args.command == "off":
        return 0 if operation and operation.get("result") in {
            "ACTIVE", "INACTIVE", "PARTIAL",
        } else 2
    if args.command in {"status", "doctor"}:
        return 0
    return 0 if report["status"] == "ACTIVE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
