#!/usr/bin/env python3
"""Verify the rendered Prime overlay before a profile gateway is restarted."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import yaml


def fail(reason: str) -> int:
    print(json.dumps({"ok": False, "reason": reason}, sort_keys=True))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args()
    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError):
        return fail("READINESS_ARTIFACT_UNAVAILABLE")
    available = args.mode != "native-only"
    server = config.get("mcp_servers", {}).get("prime-agent") if isinstance(config.get("mcp_servers"), dict) else None
    if receipt.get("profile") != args.profile or receipt.get("mode") != args.mode:
        return fail("RECEIPT_PROFILE_MISMATCH")
    if receipt.get("prime_available") is not available:
        return fail("RECEIPT_AVAILABILITY_MISMATCH")
    if available and not isinstance(server, dict):
        return fail("PRIME_OVERLAY_MISSING")
    if not available and server is not None:
        return fail("NATIVE_ONLY_OVERLAY_PRESENT")
    protected = {key: config.get(key) for key in ("providers", "memory", "toolsets")}
    fingerprint = hashlib.sha256(json.dumps(protected, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    print(json.dumps({"ok": True, "profile": args.profile, "mode": args.mode, "prime_ready": available, "protected_fingerprint": fingerprint}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
