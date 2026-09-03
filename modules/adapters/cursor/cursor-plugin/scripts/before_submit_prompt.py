"""Apply provider-free token-compute admission before a Cursor request."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from _bridge import build_bridge
from lapart_token_compute import ActivationError, load_policy, load_registry


RISK_SIGNALS = frozenset({
    "credential", "deploy", "production", "delete", "migration", "payment",
    "security", "secret", "token", "rollback", "rewrite", "force",
})
MUTATION_SIGNALS = frozenset({
    "add", "apply", "build", "change", "create", "edit", "execute", "fix",
    "implement", "install", "merge", "modify", "patch", "remove", "update", "write",
})


def _activation(argument: str | None) -> tuple[Path, dict]:
    value = argument or os.environ.get("LAPART_TOKEN_COMPUTE_ACTIVATION", "")
    if not value:
        raise ActivationError("token-compute activation registry is not configured")
    registry_path = Path(value)
    policy_path = Path(__file__).resolve().parents[4] / "token-compute-optimization" / "token-compute-policy.json"
    _, policy_sha256 = load_policy(policy_path)
    registry = load_registry(registry_path, policy_sha256=policy_sha256)
    if registry.get("targets", {}).get("cursor", {}).get("token_compute") != "active":
        raise ActivationError("Cursor token-compute optimization is not active")
    return registry_path, registry


def classify(prompt: str) -> str:
    encoded = prompt.encode("utf-8")
    words = frozenset(re.findall(r"[a-z]+", prompt.casefold()))
    if words.intersection(RISK_SIGNALS) or len(encoded) > 262144:
        return "high_risk"
    if len(encoded) > 65536 or prompt.count("\n") > 200:
        return "complex"
    if words.intersection(MUTATION_SIGNALS) or len(encoded) > 8192:
        return "standard"
    return "atomic"


def main() -> int:
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--activation-registry")
        args = parser.parse_args()
        payload = json.load(sys.stdin)
        prompt = payload.get("prompt")
        conversation = payload.get("conversation_id") or payload.get("session_id")
        if not isinstance(prompt, str) or not prompt or not isinstance(conversation, str) or not conversation:
            raise ValueError("Cursor prompt or conversation identifier is missing")
        _activation(args.activation_registry)
        tier = classify(prompt)
        result = build_bridge().dispatch({
            "hook_event_name": "beforeSubmitPrompt",
            "operation": "optimization.plan",
            "input": {
                "request_reference": hashlib.sha256(
                    (conversation + "\0" + prompt).encode("utf-8")
                ).hexdigest(),
                "constraints": {
                    "task_tier": tier,
                    "items": [
                        {
                            "id": "cursor-system",
                            "category": "system_prompt",
                            "content": "Cursor SDK adapter v2.",
                            "required": True,
                            "priority": 100,
                        },
                        {
                            "id": "shared-authority",
                            "category": "authority_context",
                            "content": "Shared authority and capability discovery remain required.",
                            "required": True,
                            "priority": 100,
                        },
                        {
                            "id": "user-request",
                            "category": "selected_content",
                            "content": prompt,
                            "required": True,
                            "priority": 90,
                        },
                    ],
                },
            },
            "conversation_id": conversation,
            "session_state": "active",
        })
        if result.provider_accounting.get("provider_calls") != 0:
            raise RuntimeError("prompt admission attempted a provider call")
        if result.context_attribution is None:
            raise RuntimeError("prompt admission omitted byte attribution")
        print(json.dumps({"continue": True}))
        return 0
    except Exception as exc:
        print(json.dumps({
            "continue": False,
            "user_message": f"LapArt token-compute admission failed closed: {type(exc).__name__}.",
        }))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
