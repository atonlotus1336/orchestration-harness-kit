#!/usr/bin/env python3
"""Run an improvement report only after a successful backup command."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cadence: str, backup: str, improvement: str, evidence_dir: Path, execute: bool) -> tuple[dict, int]:
    receipt = {
        "schema_version": 1,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "cadence": cadence,
        "mode": "report_only",
        "backup": "PENDING",
        "improvement": "NOT_RUN",
    }
    if not execute:
        receipt["status"] = "DRY_RUN"
        return receipt, 0
    backup_result = subprocess.run(shlex.split(backup), check=False)
    receipt["backup"] = "PASS" if backup_result.returncode == 0 else "FAIL"
    if backup_result.returncode != 0:
        receipt["status"] = "STOPPED_BACKUP_FAILED"
        code = backup_result.returncode or 2
    else:
        improvement_result = subprocess.run(shlex.split(improvement) + ["--cadence", cadence, "--report-only"], check=False)
        receipt["improvement"] = "PASS" if improvement_result.returncode == 0 else "FAIL"
        receipt["status"] = "PASS" if improvement_result.returncode == 0 else "IMPROVEMENT_FAILED"
        code = improvement_result.returncode
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = evidence_dir / f"{cadence}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    receipt["evidence"] = str(target)
    return receipt, code


def selftest() -> int:
    with tempfile.TemporaryDirectory() as raw:
        evidence = Path(raw)
        ok = f'{shlex.quote(sys.executable)} -c "import sys;sys.exit(0)"'
        fail = f'{shlex.quote(sys.executable)} -c "import sys;sys.exit(7)"'
        # The improvement fixture accepts the required flags.
        good, good_code = run("daily", ok, ok, evidence, True)
        stopped, stopped_code = run("weekly", fail, ok, evidence, True)
        passed = good_code == 0 and good["status"] == "PASS" and stopped_code == 7 and stopped["improvement"] == "NOT_RUN"
        print(json.dumps({"cases": 2, "failures": 0 if passed else 1}, sort_keys=True))
        return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cadence", choices=("daily", "weekly", "monthly"), required=False)
    parser.add_argument("--backup-command")
    parser.add_argument("--improvement-command")
    parser.add_argument("--evidence-dir", type=Path, default=Path("maintenance-evidence"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.cadence or not args.backup_command or not args.improvement_command:
        parser.error("--cadence, --backup-command, and --improvement-command are required")
    receipt, code = run(args.cadence, args.backup_command, args.improvement_command, args.evidence_dir, args.execute)
    print(json.dumps(receipt, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
