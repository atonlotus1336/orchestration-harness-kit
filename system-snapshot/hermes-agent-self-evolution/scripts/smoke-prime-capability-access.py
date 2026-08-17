#!/usr/bin/env python3
"""Bounded live smoke for the host capability broker and existing MCP server."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prime_bridge.capability_broker import CapabilityBroker
from prime_bridge.capability_client import call
from prime_bridge.capability_ledger import CapabilityLedger
from prime_bridge.capability_policy import CapabilityPolicy
from prime_bridge.hermes_mcp_client import HermesMCPClient
from prime_bridge.inference_relay import InferenceRelay, RelayPolicy
from prime_bridge.mcp_server import PrimeToolService


async def run(args: argparse.Namespace) -> dict[str, object]:
    if args.prime_loop:
        service = PrimeToolService(
            profile=args.profile, mode="prime-available",
            policy_path=ROOT / "config/prime-profile-policy.json",
            ledger_path=args.job_ledger, profile_config=args.profile_config,
            capability_policy_path=args.policy,
            capability_ledger_path=args.ledger,
        )
        final = await service.delegate({
            "prompt": (
                "Use the Hermes capability memory.search with query "
                "'Hermes Prime capability integration'. After it returns, reply exactly PHASE4_CAPABILITY_OK"
            ),
            "workspace": str(ROOT), "project": "phase4-capability-smoke",
            "session_id": "phase4-explicit-handoff-smoke", "explicit_handoff": True,
            "max_iterations": 8, "no_progress_seconds": 180, "wall_seconds": 420,
        })
        records = service.capability_ledger.list(profile=args.profile)
        executed = [item for item in records if item["job_id"] == final["job_id"] and item["status"] == "executed"]
        return {
            "status": final["status"], "job_id": final["job_id"],
            "result_match": final.get("result_summary") == "PHASE4_CAPABILITY_OK",
            "capability_executed": bool(executed),
            "capability_count": len(executed),
        }
    policy = CapabilityPolicy.load(args.policy)
    ledger = CapabilityLedger(args.ledger, approval_ttl_seconds=policy.approval_ttl_seconds)
    broker = CapabilityBroker(policy, ledger, HermesMCPClient(args.profile_config))
    with tempfile.TemporaryDirectory(prefix="phase4-capability-") as temporary:
        relay = InferenceRelay(
            Path(temporary) / "relay.sock",
            RelayPolicy("127.0.0.1", ("127.0.0.1",), 11434),
            capability_handler=lambda request: broker.handle({
                **request, "job_id": args.job_id, "profile": args.profile,
            }),
        )
        await relay.start()
        try:
            response = await asyncio.to_thread(
                call, relay.socket_path, args.alias, {"query": args.query},
            )
        finally:
            await relay.close()
    encoded = json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "status": response.get("status"), "alias": args.alias,
        "request_id": response.get("request_id"), "response_bytes": len(encoded),
        "response_sha256": hashlib.sha256(encoded).hexdigest(),
        "capability_frames": relay.capability_accepted,
        "health": broker.health(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-config", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=ROOT / "config/prime-capability-policy.json")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--job-ledger", type=Path, default=Path("/tmp/phase4-jobs.sqlite3"))
    parser.add_argument("--profile", default="workspace")
    parser.add_argument("--job-id", default="4" * 32)
    parser.add_argument("--alias", default="memory.search")
    parser.add_argument("--query", default="Hermes Prime capability integration")
    parser.add_argument("--prime-loop", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, sort_keys=True))
    expected = result["status"] == "ok" if not args.prime_loop else (
        result["status"] == "completed" and result["result_match"] and result["capability_executed"]
    )
    return 0 if expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
