import hashlib
import json
from pathlib import Path

import pytest

from prime_bridge.job_cli import execute, load_runtime_authority
from prime_bridge.job_ledger import LedgerError

ROOT = Path(__file__).resolve().parents[2]


def create_args(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir(parents=True)
    authority = tmp_path / "route.json"
    authority.write_text(json.dumps({"provider": "hermes-ollama", "model": "glm-5.2:cloud", "api": "openai-completions", "base_url": "http://relay"}))
    image_lock = tmp_path / "image-lock.json"
    image_lock.write_text(json.dumps({"status": "READY", "source_commit": "a" * 40, "runnable_image": "local/prime@sha256:" + "b" * 64}))
    command = ["podman", "exec", "container", "prime-agent", "--mode", "rpc"]
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({
        "schema_version": "1.0",
        "source_commit": "a" * 40, "image_digest": "sha256:" + "b" * 64, "sandbox": "rootless-podman",
        "command": command, "command_fingerprint": hashlib.sha256(json.dumps(command, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "image_lock_path": "image-lock.json", "image_lock_sha256": hashlib.sha256(image_lock.read_bytes()).hexdigest(),
    }))
    return root, ["--ledger", str(tmp_path / "jobs.sqlite3"), "--root", str(root), "create", "--owner", "owner", "--profile", "home", "--project", "proj", "--session-id", "hermes1", "--workspace", str(root), "--route-authority", str(authority), "--runtime-authority", str(runtime)]


def test_json_cli_create_status_list_cancel(tmp_path):
    root, args = create_args(tmp_path)
    created = execute(args)
    prefix = ["--ledger", str(tmp_path / "jobs.sqlite3"), "--root", str(root)]
    assert execute(prefix + ["status", created["job_id"]])["status"] == "created"
    assert execute(prefix + ["list"])["jobs"][0]["job_id"] == created["job_id"]
    assert execute(prefix + ["cancel", created["job_id"]])["status"] == "cancelled"
    health = execute(prefix + ["health"])
    assert health == {"status": "ok", "ledger": "reachable", "enabled": True}


def test_cli_route_contains_no_credentials(tmp_path):
    _root, args = create_args(tmp_path)
    created = execute(args)
    assert set(created["route"]) == {"provider", "model", "api", "endpoint", "fingerprint"}
    assert "key" not in str(created).casefold()


def test_cli_steer_is_a_durable_request(tmp_path):
    root, args = create_args(tmp_path)
    created = execute(args)
    from prime_bridge.job_ledger import JobLedger
    ledger = JobLedger(tmp_path / "jobs.sqlite3", [root])
    ledger.acquire(created["job_id"], "loop", 60)
    ledger.transition(created["job_id"], "starting"); ledger.transition(created["job_id"], "running")
    prefix = ["--ledger", str(tmp_path / "jobs.sqlite3"), "--root", str(root)]
    result = execute(prefix + ["steer", created["job_id"], "focus tests"])
    assert result["status"] == "queued"
    assert ledger.pending_commands(created["job_id"])[0]["command_id"] == result["command_id"]


def test_canonical_runtime_authority_is_bound_to_phase1_image():
    runtime = load_runtime_authority(ROOT / "config/prime-runtime-authority.json")
    assert runtime["source_commit"] == "95afd319a78ae017a41241d50b013d656a0685ce"
    assert runtime["sandbox"] == "rootless-podman"


def test_route_and_runtime_authority_are_closed(tmp_path):
    _root, args = create_args(tmp_path)
    route_path = Path(args[args.index("--route-authority") + 1])
    route = json.loads(route_path.read_text()); route["credential"] = "forbidden"
    route_path.write_text(json.dumps(route))
    with pytest.raises(LedgerError, match="closed Hermes route"):
        execute(args)

    _root2, args2 = create_args(tmp_path / "two")
    runtime_path = Path(args2[args2.index("--runtime-authority") + 1])
    runtime = json.loads(runtime_path.read_text()); runtime["unexpected"] = "field"
    runtime_path.write_text(json.dumps(runtime))
    with pytest.raises(LedgerError, match="closed Prime runtime"):
        execute(args2)
