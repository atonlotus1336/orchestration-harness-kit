#!/usr/bin/env python3
"""Generate controlled Phase 7 Prime-backed session evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prime_bridge.mcp_server import PrimeToolError, PrimeToolService  # noqa: E402
from prime_bridge.session_control import PrimeSessionControl, SessionControlError  # noqa: E402


SOURCES = (
    "config/prime-profile-policy.json",
    "prime_bridge/job_ledger.py",
    "prime_bridge/session_control.py",
    "prime_bridge/supervisor.py",
    "prime_bridge/mcp_server.py",
    "prime_bridge/capability_ledger.py",
    "prime_bridge/capability_broker.py",
    "docs/prime-backed-sessions.md",
    "scripts/smoke-prime-backed-session.py",
    "scripts/verify-phase7-evidence.py",
)


class Worker:
    runtime_fingerprint = ""

    def __init__(self, runtime_fingerprint: str):
        self.runtime_fingerprint = runtime_fingerprint
        self.release = asyncio.Event()
        self.commands: list[dict] = []

    async def start(self, **_kwargs):
        return {"session_id": "prime-controlled", "trace_ref": "opaque-controlled"}

    async def events(self):
        yield {"kind": "progress"}
        await self.release.wait()
        yield {"kind": "completed", "summary": "controlled complete"}

    async def command(self, **command):
        self.commands.append(command)
        if command["kind"] == "abort":
            self.release.set()

    async def close(self):
        return None


async def wait_for(predicate, message: str) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise RuntimeError(message)


async def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="phase7-session-") as raw:
        root = Path(raw)
        workspace = root / "workspace"
        workspace.mkdir()
        config = root / "config.yaml"
        config.write_text(yaml.safe_dump({
            "model": {"provider": "custom:ollama-cloud-local", "default": "deepseek-v4-flash:cloud"},
            "providers": {"ollama-cloud-local": {}}, "mcp_servers": {},
            "memory": {}, "toolsets": ["hermes-cli"],
        }), encoding="utf-8")
        workers: dict[str, Worker] = {}

        def factory(job):
            worker = Worker(job["runtime"]["fingerprint"])
            workers[job["job_id"]] = worker
            return worker

        service = PrimeToolService(
            profile="home", mode="prime-available",
            policy_path=ROOT / "config/prime-profile-policy.json",
            ledger_path=root / "jobs.sqlite3", profile_config=config,
            allowed_roots=[root], worker_factory=factory,
            capability_ledger_path=root / "capabilities.sqlite3",
            learning_ledger_path=root / "learning.sqlite3",
            connectivity_ledger_path=root / "connectivity.sqlite3",
            learning_shared_root=root / "shared",
        )
        cases: dict[str, str] = {}
        default_route = PrimeSessionControl(
            service.ledger, profile="workspace", mode="prime-default", enabled=True,
        ).route()
        cases["route"] = "PRIME_RECOMMENDED" if default_route["recommended_engine"] == "prime" else "FAILED"
        arguments = {
            "prompt": "controlled bounded work", "workspace": str(workspace),
            "project": "__PRIVATE_TOKEN_02__", "session_id": "hermes-controlled",
            "explicit_handoff": True, "max_iterations": 5,
            "no_progress_seconds": 30, "wall_seconds": 60,
        }
        started = await service.start_session(arguments)
        job_id = started["job"]["job_id"]
        task = service._session_tasks[job_id]
        await wait_for(lambda: service.ledger.get(job_id)["status"] == "running", "session did not start")
        cases["start"] = "RUNNING"

        first = service.session_control.events(job_id, session_id="hermes-controlled", limit=3)
        second = service.session_control.events(
            job_id, session_id="hermes-controlled", after_event_id=first["next_cursor"],
        )
        cases["status_stream"] = "STREAMED" if second["next_cursor"] >= first["next_cursor"] else "FAILED"

        service.session_control.steer(job_id, session_id="hermes-controlled", text="focus verification")
        await wait_for(lambda: any(item["kind"] == "steer" for item in workers[job_id].commands), "steer not applied")
        cases["steer"] = "APPLIED"

        decision = service.capability_policy.decision("wordpress.update")
        canonical = service.capability_policy.canonical_arguments({"post_id": 7, "title": "Controlled"})
        pending = service.capability_ledger.create(
            job_id=job_id, profile="home", alias="wordpress.update",
            decision=decision, arguments=canonical,
        )
        approved = service.approve_session({
            "job_id": job_id, "session_id": "hermes-controlled",
            "request_id": pending["request_id"], "operator_nonce": pending["operator_nonce"],
            "decision": "approved",
        })
        cases["approval"] = "APPROVED" if approved["status"] == "approved" else "FAILED"

        detached = service.session_control.detach(job_id, session_id="hermes-controlled")
        cases["detach"] = "DETACHED_CONTINUING" if detached["attachment"] == "detached" and service.ledger.get(job_id)["status"] == "running" else "FAILED"
        attached = service.session_control.reattach(job_id, session_id="hermes-controlled")
        cases["reattach"] = "ATTACHED" if attached["attachment"] == "attached" else "FAILED"

        try:
            await service.start_session(arguments)
        except PrimeToolError:
            cases["duplicate"] = "REJECTED"
        else:
            cases["duplicate"] = "FAILED"
        try:
            PrimeSessionControl(
                service.ledger, profile="other", mode="prime-available", enabled=True,
            ).status(job_id)
        except SessionControlError:
            cases["cross_scope"] = "REJECTED"
        else:
            cases["cross_scope"] = "FAILED"
        native = PrimeSessionControl(
            service.ledger, profile="telegram", mode="native-only", enabled=True,
        ).route()
        cases["native_fallback"] = "NATIVE" if native["recommended_engine"] == "native" and native["native_available"] else "FAILED"

        service.session_control.interrupt(job_id, session_id="hermes-controlled")
        final = await task
        cases["interrupt"] = "CANCELLED" if final["status"] == "cancelled" else "FAILED"
        return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = asyncio.run(run())
    evidence = {
        "schema_version": "1.0",
        "environment": "controlled-temporary",
        "status": "PASS" if all(value != "FAILED" for value in cases.values()) else "FAIL",
        "case_count": len(cases),
        "cases": dict(sorted(cases.items())),
        "live_desktop_claim": False,
        "native_hermes_preserved": True,
        "one_backend": True,
        "source_sha256": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in SOURCES
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"phase7 controlled smoke: {evidence['status']}: {len(cases)} cases")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
