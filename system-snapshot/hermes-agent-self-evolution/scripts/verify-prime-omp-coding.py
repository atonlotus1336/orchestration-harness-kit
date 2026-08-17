#!/usr/bin/env python3
"""Current-source closure verifier for Phase 10 Plan 06 (Prime-to-OMP)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TESTS = [
    "tests/integration/test_omp_feasibility.py",
    "tests/integration/test_omp_policy.py",
    "tests/integration/test_omp_runtime.py",
    "tests/integration/test_omp_adapters.py",
    "tests/integration/test_omp_worker.py",
    "tests/integration/test_omp_phase_closure.py",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 10 closure verifier")
    parser.add_argument("--strict", action="store_true", help="fail on any non-zero test result")
    args = parser.parse_args()
    failures = []
    for test in REQUIRED_TESTS:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", test, "-q"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        if result.returncode != 0:
            failures.append(test)
    report = {
        "status": "PASS" if not failures else "FAIL",
        "tests_run": len(REQUIRED_TESTS),
        "failed": failures,
        "live_execution": False,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
