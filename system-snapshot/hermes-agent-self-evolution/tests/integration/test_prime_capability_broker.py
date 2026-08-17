import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml

from prime_bridge.capability_broker import CapabilityBroker, CapabilityBrokerError
from prime_bridge.capability_ledger import CapabilityLedger
from prime_bridge.capability_policy import CapabilityPolicy
from prime_bridge.hermes_mcp_client import HermesMCPClient

ROOT = Path(__file__).resolve().parents[2]


def _server(tmp_path: Path) -> Path:
    script = tmp_path / "fixture_mcp.py"
    script.write_text(
        """import asyncio, json
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
app = Server("fixture")

@app.list_tools()
async def tools():
    return [types.Tool(name=n, description=n, inputSchema={"type": "object"}) for n in ("memory_recall", "safe_action", "wordpress_update_post")]

@app.call_tool()
async def call(name, arguments):
    return [types.TextContent(type="text", text=json.dumps({"tool": name, "echo": arguments, "token": "hidden-value", "credential": "sk-" + "x" * 24}))]

async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())

asyncio.run(main())
""",
        encoding="utf-8",
    )
    return script


def _policy(tmp_path: Path) -> CapabilityPolicy:
    raw = json.loads((ROOT / "config/prime-capability-policy.json").read_text(encoding="utf-8"))
    raw["aliases"]["fixture.action"] = {
        "classification": "action", "approval": "auto",
        "server": "fixture", "tool": "safe_action",
    }
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return CapabilityPolicy.load(path)


def _client(tmp_path: Path, script: Path) -> HermesMCPClient:
    config = {
        "mcp_servers": {
            "memoryweaver-mcp": {"command": sys.executable, "args": [str(script)]},
            "fixture": {"command": sys.executable, "args": [str(script)]},
            "wordpress": {"command": sys.executable, "args": [str(script)]},
        }
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return HermesMCPClient(path)


def test_real_mcp_retrieval_and_allowed_action_are_sanitized_and_audited(tmp_path):
    policy = _policy(tmp_path)
    ledger = CapabilityLedger(tmp_path / "capabilities.sqlite3")
    broker = CapabilityBroker(policy, ledger, _client(tmp_path, _server(tmp_path)))
    retrieval = asyncio.run(broker.handle({
        "job_id": "a" * 32, "profile": "home", "alias": "memory.search",
        "arguments": {"query": "connection fix"},
    }))
    assert retrieval["status"] == "ok"
    assert retrieval["result"]["token"] == "[redacted]"
    assert retrieval["result"]["credential"] == "[redacted]"
    action = asyncio.run(broker.handle({
        "job_id": "a" * 32, "profile": "home", "alias": "fixture.action",
        "arguments": {"value": 7},
    }))
    assert action["status"] == "ok"
    assert {item["classification"] for item in ledger.list(profile="home")} == {"retrieval", "action"}


def test_denial_and_approval_pause_resume_are_profile_bound(tmp_path):
    policy = _policy(tmp_path)
    ledger = CapabilityLedger(tmp_path / "capabilities.sqlite3")
    broker = CapabilityBroker(policy, ledger, _client(tmp_path, _server(tmp_path)))
    with pytest.raises(CapabilityBrokerError, match="denied"):
        asyncio.run(broker.handle({
            "job_id": "b" * 32, "profile": "home", "alias": "shell.exec", "arguments": {},
        }))
    paused = asyncio.run(broker.handle({
        "job_id": "b" * 32, "profile": "home", "alias": "wordpress.update",
        "arguments": {"post_id": 1, "title": "Reviewed"},
    }))
    assert paused["status"] == "approval_required"
    pending = ledger.get(paused["request_id"])
    ledger.resolve(
        paused["request_id"], profile="home", decision="approved",
        approver="native-session", operator_nonce=pending["operator_nonce"],
    )
    with pytest.raises(CapabilityBrokerError, match="binding"):
        asyncio.run(broker.handle({
            "job_id": "b" * 32, "profile": "other", "alias": "wordpress.update",
            "arguments": {"post_id": 1, "title": "Reviewed"}, "request_id": paused["request_id"],
        }))
    resumed = asyncio.run(broker.handle({
        "job_id": "b" * 32, "profile": "home", "alias": "wordpress.update",
        "arguments": {"post_id": 1, "title": "Reviewed"}, "request_id": paused["request_id"],
    }))
    assert resumed["status"] == "ok"
    assert ledger.get(paused["request_id"])["approval_provenance"]["approver"] == "native-session"


def test_health_distinguishes_policy_transport_and_server_readiness(tmp_path):
    script = _server(tmp_path)
    client = _client(tmp_path, script)
    broker = CapabilityBroker(_policy(tmp_path), CapabilityLedger(tmp_path / "cap.sqlite3"), client)
    health = broker.health()
    assert health["liveness"] == "ready"
    assert health["policy"] == "ready"
    assert health["mcp_transport"] == "configured"
    assert health["retrieval"] == "configured"
    assert health["authentication"] == "host-owned"
