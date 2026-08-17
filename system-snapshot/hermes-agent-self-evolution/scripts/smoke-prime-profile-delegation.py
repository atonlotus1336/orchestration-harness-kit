#!/usr/bin/env python3
"""Run one bounded explicit handoff from an existing Hermes profile."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from prime_bridge.mcp_server import PrimeToolService


ROOT = Path(__file__).resolve().parents[1]


async def run(profile: str, profile_root: Path, ledger: Path, workspace: Path) -> dict:
    service = PrimeToolService(
        profile=profile, mode="prime-available",
        policy_path=ROOT / "config/prime-profile-policy.json",
        ledger_path=ledger, profile_config=profile_root / profile / "config.yaml",
    )
    final = await service.delegate({
        "prompt": "This is a bounded integration smoke test. Reply exactly PHASE3_PRIME_OK",
        "workspace": str(workspace), "project": "phase3-profile-overlay-smoke",
        "session_id": "phase3-explicit-handoff-smoke", "explicit_handoff": True,
        "max_iterations": 6, "no_progress_seconds": 180, "wall_seconds": 420,
    })
    return {
        "status": final["status"], "job_id": final["job_id"],
        "profile": final["profile"], "session_id": final["session_id"],
        "result_summary": final.get("result_summary"),
        "last_error": final.get("last_error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="home")
    parser.add_argument("--profile-root", type=Path, default=Path("/home/__LINUX_USER__/.hermes/profiles"))
    parser.add_argument("--ledger", type=Path, default=Path("/home/__LINUX_USER__/.hermes/state/prime/jobs.sqlite3"))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    args = parser.parse_args()
    result = asyncio.run(run(args.profile, args.profile_root, args.ledger, args.workspace))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "completed" and result["result_summary"] == "PHASE3_PRIME_OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
