#!/usr/bin/env python3
"""Prepare a disabled, provenance-bound local Prime/OMP broker configuration."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(prime: Path, omp: Path, prime_expected: str, omp_expected: str) -> tuple[dict, list[str]]:
    errors: list[str] = []
    for name, path in (("prime", prime), ("omp", omp)):
        if not path.is_file():
            errors.append(f"{name} executable is missing")
    prime_actual = sha256(prime) if prime.is_file() else ""
    omp_actual = sha256(omp) if omp.is_file() else ""
    if prime_expected and prime_actual != prime_expected.lower():
        errors.append("prime executable checksum mismatch")
    if omp_expected and omp_actual != omp_expected.lower():
        errors.append("omp executable checksum mismatch")
    config = {
        "schema_version": 1,
        "enabled": False,
        "prime": {"executable": str(prime), "sha256": prime_actual},
        "omp": {"executable": str(omp), "sha256": omp_actual},
        "contract": "prime-omp-contract.json",
        "worker_workspace": "disposable-worktree",
        "activation_note": "Review provenance and run contract verification before setting enabled=true."
    }
    return config, errors


def selftest() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        prime = root / "prime"
        omp = root / "omp"
        prime.write_bytes(b"prime-test")
        omp.write_bytes(b"omp-test")
        good, good_errors = prepare(prime, omp, sha256(prime), sha256(omp))
        _, bad_errors = prepare(prime, omp, "0" * 64, sha256(omp))
        passed = not good_errors and good["enabled"] is False and "checksum mismatch" in " ".join(bad_errors)
        print(json.dumps({"cases": 2, "failures": 0 if passed else 1}, sort_keys=True))
        return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prime-executable", type=Path)
    parser.add_argument("--omp-executable", type=Path)
    parser.add_argument("--prime-sha256", default="")
    parser.add_argument("--omp-sha256", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.prime_executable or not args.omp_executable or not args.output:
        parser.error("--prime-executable, --omp-executable, and --output are required")
    config, errors = prepare(args.prime_executable, args.omp_executable, args.prime_sha256, args.omp_sha256)
    if errors:
        print(json.dumps({"status": "RED", "errors": errors}, sort_keys=True))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "GREEN", "output": str(args.output), "enabled": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
