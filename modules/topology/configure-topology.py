#!/usr/bin/env python3
"""Validate and render solo-first topology profiles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROLES = {"solo", "vps", "second-pc"}


def validate(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["topology must be a JSON object"]
    errors: list[str] = []
    profiles = value.get("profiles")
    if value.get("default_role") != "solo":
        errors.append("default_role must be solo")
    if not isinstance(profiles, dict) or set(profiles) != ROLES:
        return errors + ["profiles must contain exactly solo, vps, and second-pc"]
    authorities = [name for name, profile in profiles.items() if profile.get("storage_authority") is True]
    if authorities != ["solo"]:
        errors.append("solo must be the only storage authority")
    for name, profile in profiles.items():
        gateway = profile.get("gateway", {})
        dashboard = profile.get("dashboard", {})
        if gateway.get("authentication_required") is not True:
            errors.append(f"{name}: gateway authentication is required")
        if dashboard.get("bind") != "127.0.0.1":
            errors.append(f"{name}: dashboard must stay loopback-bound")
        if name != "solo" and profile.get("home_disk_access") != "authenticated_api":
            errors.append(f"{name}: remote home-disk access must use an authenticated API")
        if name != "solo" and profile.get("remote_verification_required") is not True:
            errors.append(f"{name}: remote-side verification is required")
    mount = value.get("direct_network_mount", {})
    if mount.get("enabled") is not False or mount.get("single_writer_required") is not True:
        errors.append("direct network mounts must default off and require one writer")
    return errors


def render(value: dict, role: str, machine_id: str) -> dict:
    return {
        "schema_version": value["schema_version"],
        "machine_id": machine_id,
        "role": role,
        "profile": value["profiles"][role],
        "service_addresses": {
            "storage_authority": "<local-value>",
            "gateway": "<local-value>",
            "memory_api": "<local-value>"
        },
        "enabled": False,
        "activation_note": "Review local values and set enabled=true only after verification passes."
    }


def selftest(source: Path) -> int:
    base = json.loads(source.read_text(encoding="utf-8"))
    cases: list[tuple[dict, bool]] = [(base, False)]
    unsafe = json.loads(json.dumps(base))
    unsafe["profiles"]["second-pc"]["storage_authority"] = True
    cases.append((unsafe, True))
    unsafe = json.loads(json.dumps(base))
    unsafe["profiles"]["vps"]["dashboard"]["bind"] = "0.0.0.0"
    cases.append((unsafe, True))
    unsafe = json.loads(json.dumps(base))
    unsafe["profiles"]["second-pc"]["home_disk_access"] = "network_mount"
    cases.append((unsafe, True))
    failures = sum(bool(validate(case)) != should_fail for case, should_fail in cases)
    print(json.dumps({"cases": len(cases), "failures": failures}, sort_keys=True))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=Path(__file__).with_name("topology-profiles.json"))
    parser.add_argument("--role", choices=sorted(ROLES))
    parser.add_argument("--machine-id", default="<local-value>")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest(args.profiles)
    try:
        value = json.loads(args.profiles.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "RED", "errors": [f"profiles unreadable: {exc}"]}))
        return 2
    errors = validate(value)
    if errors:
        print(json.dumps({"status": "RED", "errors": errors}, sort_keys=True))
        return 2
    if args.output:
        if not args.role:
            parser.error("--role is required with --output")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(render(value, args.role, args.machine_id), indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "GREEN", "default_role": "solo", "rendered": bool(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
