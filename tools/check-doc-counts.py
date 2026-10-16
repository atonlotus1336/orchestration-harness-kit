#!/usr/bin/env python3
"""Fail when a doc states a dry-run-gate test count the suite does not report.

Runs `modules/guards/test-dry-run-gate.py`, reads its "N passed, M failed"
line, and compares N with every count the docs state. An anchor phrase that
no longer appears in its doc is also a failure, so rewording a doc cannot
silently switch the check off.

Usage:
  python tools/check-doc-counts.py              # run the suite, check docs
  python tools/check-doc-counts.py --expected 67   # skip the suite run
  python tools/check-doc-counts.py --selftest   # replay the failing inputs
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = Path("modules/guards/test-dry-run-gate.py")
RESULT = re.compile(r"^(\d+) passed, (\d+) failed\s*$", re.MULTILINE)

# (doc, anchor regex). Group 1 of each anchor is the stated count.
CLAIMS: list[tuple[str, str]] = [
    ("README.md", r"its own (\d+) tests"),
    ("README.md", r"its self-test, (\d+) cases"),
    ("modules/guards/README.md", r"the proof\. (\d+) cases"),
    ("AGENTS.md", r"test-dry-run-gate\.py` \((\d+) cases\)"),
    ("KIT-MAINTENANCE.md", r"test-dry-run-gate\.py\s+# expect: (\d+) passed"),
]


def suite_count(root: Path = ROOT) -> int:
    """Run the dry-run-gate suite; return its pass count or raise on failure."""
    result = subprocess.run(
        [sys.executable, str(SUITE)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    found = RESULT.findall(result.stdout)
    if not found:
        raise RuntimeError(f"{SUITE} printed no 'N passed, M failed' line (exit {result.returncode})")
    passed, failed = (int(value) for value in found[-1])
    if failed or result.returncode:
        raise RuntimeError(f"{SUITE} reports {failed} failed (exit {result.returncode})")
    return passed


def check(root: Path, actual: int) -> list[str]:
    """Return one failure line per missing anchor or wrong stated count."""
    failures = []
    for relative, anchor in CLAIMS:
        path = root / relative
        if not path.is_file():
            failures.append(f"{relative}: file missing")
            continue
        text = path.read_text(encoding="utf-8")
        matches = list(re.finditer(anchor, text))
        if not matches:
            failures.append(f"{relative}: anchor not found: {anchor}")
            continue
        for match in matches:
            stated = int(match.group(1))
            if stated != actual:
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"{relative}:{line}: states {stated}, suite reports {actual}")
    return failures


def selftest() -> list[str]:
    """Replay named failing inputs against copies of the docs."""
    problems = []
    actual = 67
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for relative in {doc for doc, _ in CLAIMS}:
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        clean = {
            "README.md": f"its own {actual} tests\nits self-test, {actual} cases\n",
            "modules/guards/README.md": f"the proof. {actual} cases\n",
            "AGENTS.md": f"`modules/guards/test-dry-run-gate.py` ({actual} cases)\n",
            "KIT-MAINTENANCE.md": f"python modules/guards/test-dry-run-gate.py   # expect: {actual} passed, 0 failed\n",
        }
        for relative, text in clean.items():
            (base / relative).write_text(text, encoding="utf-8")
        if check(base, actual):
            problems.append("clean fixture reported failures")

        cases = [
            # The 2026-10-16 drift: README said 32 while the suite ran 63+.
            ("wrong count", "README.md", f"its own 32 tests\nits self-test, {actual} cases\n", "README.md:1: states 32"),
            ("reworded anchor", "AGENTS.md", f"`modules/guards/test-dry-run-gate.py` (about {actual})\n", "AGENTS.md: anchor not found"),
            ("missing doc", "KIT-MAINTENANCE.md", None, "KIT-MAINTENANCE.md: file missing"),
        ]
        for name, relative, broken, expected in cases:
            path = base / relative
            if broken is None:
                path.unlink()
            else:
                path.write_text(broken, encoding="utf-8")
            failures = check(base, actual)
            if not any(failure.startswith(expected) for failure in failures):
                problems.append(f"{name}: expected '{expected}', got {failures}")
            path.write_text(clean[relative], encoding="utf-8")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expected", type=int, help="use this count instead of running the suite")
    parser.add_argument("--selftest", action="store_true", help="replay the failing inputs and exit")
    args = parser.parse_args()
    if args.selftest:
        problems = selftest()
        for problem in problems:
            print("SELFTEST FAIL: " + problem)
        print("selftest " + ("FAILED" if problems else "passed"))
        return 1 if problems else 0
    try:
        actual = args.expected if args.expected is not None else suite_count()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}")
        return 1
    failures = check(ROOT, actual)
    for failure in failures:
        print("FAIL: " + failure)
    if not failures:
        print(f"doc counts match: {len(CLAIMS)} claims, {actual} tests")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
