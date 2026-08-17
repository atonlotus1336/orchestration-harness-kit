#!/usr/bin/env python3
"""Fail-closed verifier for backup-gated maintenance schedules."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CADENCES = {"daily", "weekly", "monthly"}


def verify(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["schedule must be a JSON object"]
    errors: list[str] = []
    if value.get("activation") != "manual_report_only":
        errors.append("activation must be manual_report_only")
    if value.get("production_write_requires_human_confirmation") is not True:
        errors.append("production writes must require human confirmation")
    evidence = value.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("backup_receipt_required") is not True or evidence.get("report_required") is not True:
        errors.append("backup receipt and report must both be required")
    chains = value.get("chains")
    if not isinstance(chains, list) or {item.get("cadence") for item in chains if isinstance(item, dict)} != CADENCES:
        errors.append("chains must contain exactly daily, weekly, and monthly")
        return errors
    for chain in chains:
        if not isinstance(chain, dict) or chain.get("backup_must_succeed") is not True or chain.get("scope") != "sandbox_analysis":
            errors.append(f"unsafe chain: {chain.get('cadence', 'unknown') if isinstance(chain, dict) else 'invalid'}")
    return errors


def selftest() -> int:
    base = json.loads(Path(__file__).with_name("maintenance-schedule.json").read_text(encoding="utf-8"))
    unsafe = json.loads(json.dumps(base))
    unsafe["chains"][0]["backup_must_succeed"] = False
    missing = json.loads(json.dumps(base))
    missing["chains"] = missing["chains"][:-1]
    failures = sum(bool(verify(case)) != should_fail for case, should_fail in [(base, False), (unsafe, True), (missing, True)])
    print(json.dumps({"cases": 3, "failures": failures}, sort_keys=True))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("schedule", nargs="?", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.schedule is None:
        parser.error("schedule is required unless --selftest is used")
    try:
        errors = verify(json.loads(args.schedule.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors = [f"schedule is unreadable: {exc}"]
    print(json.dumps({"status": "GREEN" if not errors else "RED", "errors": errors}, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
