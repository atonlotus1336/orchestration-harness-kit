#!/usr/bin/env python3
"""Backup and activate validated Prime overlays for the next Hermes restart."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from prime_bridge.profile_policy import (
    PRESERVED_SECTIONS,
    PrimeProfilePolicy,
    restriction_projection,
    unmanaged_mcp_servers,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.profiles_root.resolve(strict=True)
    if root != Path("/home/__LINUX_USER__/.hermes/profiles"):
        raise SystemExit("activation is restricted to the canonical WSL profiles root")
    policy = PrimeProfilePolicy.load(args.policy)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = args.backup_root.resolve() / timestamp
    if root == backup or root in backup.parents:
        raise SystemExit("backup root must be outside the profiles tree")
    prepared = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        source = directory / "config.yaml"
        candidate = directory / "config.prime-next.yaml"
        receipt_path = directory / "prime-overlay-receipt.json"
        if not source.is_file() or not candidate.is_file() or not receipt_path.is_file():
            continue
        original = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        rendered = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        for section in PRESERVED_SECTIONS:
            if section != "mcp_servers" and original.get(section) != rendered.get(section):
                raise SystemExit(f"{directory.name}: preserved section changed")
        overlaid = rendered.get("mcp_servers") or {}
        if unmanaged_mcp_servers(overlaid) != unmanaged_mcp_servers(original.get("mcp_servers") or {}):
            raise SystemExit(f"{directory.name}: existing MCP server changed")
        expected = policy.permits(directory.name)
        if ("prime-agent" in overlaid) != expected:
            raise SystemExit(f"{directory.name}: policy exposure mismatch")
        if receipt.get("restriction_fingerprint") != restriction_projection(original)["fingerprint"]:
            raise SystemExit(f"{directory.name}: restriction receipt mismatch")
        prepared.append((directory.name, source, candidate, sha256(source), sha256(candidate)))
    if not prepared:
        raise SystemExit("no validated overlays are staged")
    backup.mkdir(parents=True, exist_ok=False)
    manifest = []
    for profile, source, candidate, before_hash, after_hash in prepared:
        target_backup = backup / profile / "config.yaml"
        target_backup.parent.mkdir(parents=True)
        shutil.copy2(source, target_backup)
        os.replace(candidate, source)
        if sha256(source) != after_hash or sha256(target_backup) != before_hash:
            raise SystemExit(f"{profile}: activation hash verification failed")
        manifest.append({
            "profile": profile, "mode": policy.mode_for(profile),
            "before_sha256": before_hash, "after_sha256": after_hash,
            "backup": str(target_backup),
        })
    manifest_path = backup / "activation-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "1.0", "status": "PASS", "activation": "restart-boundary",
        "services_restarted": False, "profiles": manifest,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "profiles": len(manifest), "backup": str(backup),
        "services_restarted": False, "activation": "restart-boundary",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
