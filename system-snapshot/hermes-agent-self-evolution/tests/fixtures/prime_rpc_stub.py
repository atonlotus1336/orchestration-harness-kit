"""Deterministic Prime RPC-shaped subprocess used only by adapter tests."""

import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    kind = request["type"]
    data = {"sessionId": "stub-session", "sessionFile": "/tmp/stub.jsonl"} if kind == "get_state" else {}
    valid = kind != "steer" or request["id"] == "e" * 32
    print(json.dumps({"id": request["id"], "type": "response", "command": kind, "success": valid, "data": data}), flush=True)
    if kind == "prompt":
        print(json.dumps({"type": "turn_end"}), flush=True)
        print(json.dumps({"type": "agent_end"}), flush=True)
