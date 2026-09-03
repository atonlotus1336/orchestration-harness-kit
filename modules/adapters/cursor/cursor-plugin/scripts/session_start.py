"""Inject a compact readiness statement at Cursor session start."""

from __future__ import annotations

import json
import sys

from _bridge import build_bridge


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id") or payload.get("conversation_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Cursor session identifier is missing")
        result = build_bridge().initialize(session_id=session_id)
        if result.provider_accounting.get("provider_calls") != 0:
            raise RuntimeError("initialization attempted a provider call")
        print(json.dumps({
            "additional_context": (
                "LapArt orchestration protocol v2 is ready. Shared authority, routing, "
                "progressive capability discovery, token optimization, accounting, and "
                "rollback remain core-owned. Live activation stays unchanged."
            )
        }))
        return 0
    except Exception as exc:
        print(json.dumps({"additional_context": f"LapArt adapter unavailable: {type(exc).__name__}."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
