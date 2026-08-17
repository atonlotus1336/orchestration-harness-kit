#!/usr/bin/env python3
"""Fail-closed verifier for the portable Prime/OMP integration contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_FORBIDDEN = {
    "authoritative_repo_write", "direct_patch_apply", "container_socket",
    "shared_memory", "tool_discovery", "worker_delegation", "alternate_model_provider",
}
REQUIRED_FLOW = {
    "worker_returns_patch": True, "hermes_applies_patch": True,
    "hermes_runs_verification": True, "audit_receipt_required": True,
}
ALLOWED_MODES = {"read_only_analysis", "patch_preview", "disposable_worker"}


def verify(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["policy must be a JSON object"]
    errors: list[str] = []
    if value.get("enabled") is not False:
        errors.append("enabled must default to false")
    if value.get("controller") != "prime-agent":
        errors.append("controller must be prime-agent")
    if value.get("worker") != "oh-my-pi":
        errors.append("worker must be oh-my-pi")
    if value.get("worker_workspace") != "disposable-worktree":
        errors.append("worker_workspace must be disposable-worktree")
    modes = value.get("allowed_modes")
    if not isinstance(modes, list) or set(modes) - ALLOWED_MODES or "disposable_worker" not in modes:
        errors.append("allowed_modes must be a subset containing disposable_worker")
    forbidden = value.get("forbidden_capabilities")
    if not isinstance(forbidden, list) or not REQUIRED_FORBIDDEN.issubset(set(forbidden)):
        errors.append("forbidden_capabilities is missing a required denial")
    flow = value.get("patch_flow")
    if not isinstance(flow, dict) or any(flow.get(key) is not expected for key, expected in REQUIRED_FLOW.items()):
        errors.append("patch_flow must require Hermes apply, verification, and an audit receipt")
    return errors


def selftest() -> int:
    base = json.loads(Path(__file__).with_name("prime-omp-contract.json").read_text(encoding="utf-8"))
    cases = [(base, False)]
    for key in REQUIRED_FORBIDDEN:
        mutated = dict(base)
        mutated["forbidden_capabilities"] = [item for item in base["forbidden_capabilities"] if item != key]
        cases.append((mutated, True))
    unsafe = dict(base)
    unsafe["patch_flow"] = dict(base["patch_flow"], hermes_applies_patch=False)
    cases.append((unsafe, True))
    failures = sum(bool(verify(case)) != should_fail for case, should_fail in cases)
    print(json.dumps({"cases": len(cases), "failures": failures}, sort_keys=True))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("policy", nargs="?", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.policy is None:
        parser.error("policy is required unless --selftest is used")
    try:
        errors = verify(json.loads(args.policy.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors = [f"policy is unreadable: {exc}"]
    print(json.dumps({"status": "GREEN" if not errors else "RED", "errors": errors}, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
