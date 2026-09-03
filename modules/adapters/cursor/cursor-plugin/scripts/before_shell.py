"""Apply the shared authority decision to risky Cursor shell commands."""

from __future__ import annotations

import importlib.util
import json
import re
import sys

from _bridge import KIT_ROOT, build_bridge


def _guard():
    path = KIT_ROOT / "modules/guards/dry-run-gate.py"
    spec = importlib.util.spec_from_file_location("cursor_dry_run_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload.get("command")
        conversation = payload.get("conversation_id") or payload.get("session_id") or "cursor-shell"
        if not isinstance(command, str) or not command:
            raise ValueError("Cursor shell command is missing")
        gate = _guard()
        analysis = gate.analyze_command(command, gate.load_policy()[0])
        if analysis is None:
            print(json.dumps({"permission": "allow"}))
            return 0
        history_pattern = r"\bgit\s+(?:reset\b[^\n]*--hard|push\b[^\n]*(?:--force(?!-with-lease)|\s-f\b))"
        hard_history = bool(re.search(history_pattern, command))
        local_event = {
            "hook_event_name": "beforeShellExecution",
            "operation": "authority.decide",
            "input": {
                "action": "rewrite_history" if hard_history else "delete",
                "resource": "cursor-shell-command",
                "risk": "high",
            },
            "conversation_id": str(conversation),
            "session_state": "active",
        }
        decision = build_bridge().dispatch(local_event).local_response["output"]["decision"]
        permission = "deny" if decision == "deny" else "ask"
        print(json.dumps({
            "permission": permission,
            "user_message": "LapArt requires review for this potentially destructive command.",
            "agent_message": "Do not bypass the authority gate. Request explicit user approval.",
        }))
        return 0
    except Exception as exc:
        print(json.dumps({
            "permission": "deny",
            "user_message": f"LapArt safety hook failed closed: {type(exc).__name__}.",
            "agent_message": "Repair or disable the isolated plugin before retrying this risky command.",
        }))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
