"""Deterministic Prime RPC-shaped subprocess used only by adapter tests."""

import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    kind = request["type"]
    if kind == "get_state":
        data = {"sessionId": "stub-session", "sessionFile": "/tmp/stub.jsonl"}
    elif kind == "get_session_stats":
        data = {"sessionId": "stub-session", "tokens": {"input": 5, "output": 2, "total": 7}}
    else:
        data = {}
    valid = kind != "steer" or request["id"] == "e" * 32
    print(json.dumps({"id": request["id"], "type": "response", "command": kind, "success": valid, "data": data}), flush=True)
    if kind == "prompt":
        print(json.dumps({"type": "turn_end"}), flush=True)
        print(json.dumps({"type": "agent_end"}), flush=True)
