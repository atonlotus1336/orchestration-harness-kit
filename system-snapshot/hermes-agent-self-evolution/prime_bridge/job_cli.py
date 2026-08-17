"""Secret-free JSON operator surface for the Hermes Prime job ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from prime_bridge.job_ledger import JobLedger, LedgerError


def load_runtime_authority(path: Path, *, authority: dict[str, Any] | None = None) -> dict[str, str]:
    authority = authority if authority is not None else json.loads(path.read_text(encoding="utf-8"))
    runtime_keys = ("source_commit", "image_digest", "sandbox", "command_fingerprint")
    authority_keys = {"schema_version", *runtime_keys, "command", "image_lock_path", "image_lock_sha256"}
    if not isinstance(authority, dict) or set(authority) != authority_keys or not all(isinstance(authority.get(key), str) for key in runtime_keys):
        raise LedgerError("closed Prime runtime authority required")
    command = authority.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise LedgerError("runtime command contract required")
    command_hash = hashlib.sha256(json.dumps(command, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if command_hash != authority["command_fingerprint"]:
        raise LedgerError("runtime command authority mismatch")
    lock_path = (path.parent / authority.get("image_lock_path", "")).resolve(strict=True)
    lock_bytes = lock_path.read_bytes()
    if hashlib.sha256(lock_bytes).hexdigest() != authority.get("image_lock_sha256"):
        raise LedgerError("Prime image lock hash mismatch")
    lock = json.loads(lock_bytes)
    if (
        lock.get("status") != "READY"
        or lock.get("source_commit") != authority["source_commit"]
        or not str(lock.get("runnable_image", "")).endswith("@" + authority["image_digest"])
    ):
        raise LedgerError("runtime authority does not match the READY image lock")
    runtime = {key: authority[key] for key in runtime_keys}
    return {**runtime, "fingerprint": JobLedger.runtime_fingerprint(runtime)}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="hermes-prime-job")
    value.add_argument("--ledger", type=Path, required=True)
    value.add_argument("--root", type=Path, action="append", required=True)
    sub = value.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    for name in ("owner", "profile", "project", "session-id", "workspace", "route-authority", "runtime-authority"):
        create.add_argument(f"--{name}", required=True)
    create.add_argument("--max-iterations", type=int, default=20)
    create.add_argument("--no-progress-seconds", type=int, default=300)
    create.add_argument("--wall-seconds", type=int, default=3600)
    status = sub.add_parser("status"); status.add_argument("job_id")
    listing = sub.add_parser("list"); listing.add_argument("--profile")
    steer = sub.add_parser("steer"); steer.add_argument("job_id"); steer.add_argument("text")
    cancel = sub.add_parser("cancel"); cancel.add_argument("job_id")
    sub.add_parser("health")
    return value


def execute(argv: list[str]) -> dict[str, Any]:
    args = parser().parse_args(argv)
    ledger = JobLedger(args.ledger, args.root)
    if args.command == "create":
        authority = json.loads(Path(args.route_authority).read_text(encoding="utf-8"))
        route_keys = {"provider", "model", "api", "base_url"}
        if not isinstance(authority, dict) or set(authority) != route_keys or not all(isinstance(authority.get(key), str) and authority[key] for key in route_keys):
            raise LedgerError("closed Hermes route authority required")
        public = {"provider": authority["provider"], "model": authority["model"], "api": authority["api"], "endpoint": authority["base_url"]}
        route = {**public, "fingerprint": JobLedger.route_fingerprint(public)}
        runtime = load_runtime_authority(Path(args.runtime_authority).resolve(strict=True))
        return ledger.create(
            owner=args.owner, profile=args.profile, project=args.project, session_id=args.session_id,
            workspace=args.workspace, route=route,
            runtime=runtime,
            limits={"max_iterations": args.max_iterations, "no_progress_seconds": args.no_progress_seconds, "wall_seconds": args.wall_seconds},
        )
    if args.command == "status": return ledger.get(args.job_id)
    if args.command == "list": return {"jobs": ledger.list(profile=args.profile)}
    if args.command == "steer": return {"command_id": ledger.enqueue_command(args.job_id, "steer", args.text), "status": "queued"}
    if args.command == "cancel":
        job = ledger.get(args.job_id)
        if job["status"] in {"completed", "failed", "cancelled", "orphaned"}: return job
        if job["status"] == "created": return ledger.transition(args.job_id, "cancelled")
        return {"command_id": ledger.enqueue_command(args.job_id, "cancel"), "status": "queued"}
    return {"status": "ok", "ledger": "reachable", "enabled": os.environ.get("HERMES_PRIME_ENABLED", "1") == "1"}


def main() -> int:
    try:
        print(json.dumps(execute(os.sys.argv[1:]), sort_keys=True, separators=(",", ":")))
        return 0
    except (LedgerError, OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
