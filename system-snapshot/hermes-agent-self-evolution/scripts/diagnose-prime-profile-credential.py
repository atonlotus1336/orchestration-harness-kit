#!/usr/bin/env python3
"""Print only public credential-chain metadata for a Hermes profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from prime_bridge.model_route import resolve_hermes_provider_credential


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    provider = str((config.get("model") or {}).get("provider", "")).removeprefix("custom:")
    profile_root = args.config.parent
    hermes_root = profile_root.parents[1]
    candidates = [profile_root / "auth.json", hermes_root / "auth.json"]
    auth_metadata = []
    for path in candidates:
        keys = []
        sources = []
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            pool = raw.get("credential_pool") or {}
            keys = sorted(pool)
            entries = pool.get(provider) or pool.get(f"custom:{provider}") or []
            sources = [entry.get("source") for entry in entries if isinstance(entry, dict)]
        auth_metadata.append({"path": str(path), "exists": path.is_file(), "pool_keys": keys, "provider_sources": sources})
    attempts = []
    for auth_path in candidates:
        result = resolve_hermes_provider_credential(
            provider, args.config, auth_path=auth_path,
            env_path=profile_root / ".env",
        )
        attempts.append({"auth_path": str(auth_path), **result.public_evidence()})
    print(json.dumps({
        "provider": provider, "provider_config_present": provider in (config.get("providers") or {}),
        "profile_env_present": (profile_root / ".env").is_file(),
        "root_env_present": (hermes_root / ".env").is_file(),
        "auth": auth_metadata, "attempts": attempts,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
