"""Inject a compact readiness statement at Cursor session start."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from _bridge import build_bridge
from lapart_token_compute import ActivationError, load_policy, load_registry


def _registry_path(argument: str | None) -> Path:
    value = argument or os.environ.get("LAPART_TOKEN_COMPUTE_ACTIVATION", "")
    if not value:
        raise ActivationError("token-compute activation registry is not configured")
    return Path(value)


def _active_cursor(registry_path: Path) -> None:
    policy_path = Path(__file__).resolve().parents[4] / "token-compute-optimization" / "token-compute-policy.json"
    _, policy_sha256 = load_policy(policy_path)
    registry = load_registry(registry_path, policy_sha256=policy_sha256)
    if registry.get("targets", {}).get("cursor", {}).get("token_compute") != "active":
        raise ActivationError("Cursor token-compute optimization is not active")


def main() -> int:
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--activation-registry")
        args = parser.parse_args()
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id") or payload.get("conversation_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Cursor session identifier is missing")
        registry_path = _registry_path(args.activation_registry)
        _active_cursor(registry_path)
        result = build_bridge().initialize(session_id=session_id)
        if result.provider_accounting.get("provider_calls") != 0:
            raise RuntimeError("initialization attempted a provider call")
        print(json.dumps({
            "env": {"LAPART_TOKEN_COMPUTE_ACTIVATION": str(registry_path)},
            "additional_context": (
                "LapArt orchestration protocol v2 and token-compute optimization are active. "
                "Budgets, full-depth fallback, progressive discovery, accounting, and rollback "
                "remain core-owned; marginal-value stopping remains shadow-only."
            )
        }))
        return 0
    except Exception as exc:
        print(json.dumps({"additional_context": f"LapArt adapter unavailable: {type(exc).__name__}."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
