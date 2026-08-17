#!/usr/bin/env python3
"""Fail closed when the Phase 9 routing seams or canonical policy drift."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    required = {
        ROOT / "config/prime-auto-route-policy.json": '"ponytail"',
        ROOT / "prime_bridge/intent_router.py": "def classify",
        ROOT / "prime_bridge/auto_handoff.py": "async def auto_handoff",
        ROOT / "prime_bridge/mcp_server.py": "prime_auto_session_start",
        ROOT.parent / ".hermes/hermes-agent/gateway/platforms/api_server.py": "_maybe_prime_auto_handoff",
        ROOT.parent / ".hermes/hermes-agent/tui_gateway/methods_prompt.py": "_prime_auto_route",
    }
    failures = [str(path) for path, token in required.items() if not path.is_file() or token not in path.read_text(encoding="utf-8", errors="replace")]
    skill = ROOT.parent / "skills-shared/ponytail/skills/ponytail/SKILL.md"
    if not skill.is_file():
        failures.append("canonical Ponytail skill missing")
    result = {
        "status": "PASS" if not failures else "FAIL", "failures": failures,
        "ponytail_sha256": hashlib.sha256(skill.read_bytes()).hexdigest() if skill.is_file() else None,
        "live_execution": False,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
