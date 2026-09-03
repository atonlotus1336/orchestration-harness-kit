"""Isolated session, activation, rollback, memory, and telemetry state."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from .errors import CoreError
from .policy import CorePolicy


class CoreState:
    def __init__(self):
        self.sessions: dict[str, dict[str, Any]] = {}
        self.activation: dict[str, str] = defaultdict(lambda: "inactive")
        self.rollback_points: dict[str, tuple[str, str]] = {}
        self.memory: dict[str, list[Any]] = defaultdict(list)
        self.telemetry: list[dict[str, Any]] = []
        self.mcp_connections: set[str] = set()

    def transition_activation(self, payload: dict, policy: CorePolicy) -> dict:
        profile = payload["profile"]
        previous = payload["previous_state"]
        requested = payload["requested_state"]
        if previous not in policy.activation_states or requested not in policy.activation_states:
            raise CoreError("ACTIVATION_STATE_INVALID", "Activation state is invalid.")
        if self.activation[profile] != previous:
            raise CoreError("ACTIVATION_STATE_STALE", "Previous activation state does not match.")
        if requested == "production" and not policy.production_activation_allowed:
            raise CoreError("PRODUCTION_ACTIVATION_DISABLED", "Production activation is disabled.")
        if not payload.get("approval_reference"):
            raise CoreError("APPROVAL_REQUIRED", "Activation requires an approval reference.")
        if not policy.required_promotion_gates.issubset(set(payload.get("gates", []))):
            raise CoreError("PROMOTION_GATES_INCOMPLETE", "Activation gates are incomplete.")
        rollback_reference = payload["rollback_reference"]
        self.rollback_points[rollback_reference] = (profile, previous)
        self.activation[profile] = requested
        return {"state": requested, "activated": True}

    def rollback(self, payload: dict) -> dict:
        reference = payload["rollback_reference"]
        point = self.rollback_points.get(reference)
        if point is None:
            raise CoreError("ROLLBACK_NOT_FOUND", "Rollback reference is not available.")
        profile, previous = point
        if payload["target_state"] != previous:
            raise CoreError("ROLLBACK_TARGET_INVALID", "Rollback target differs from the saved state.")
        self.activation[profile] = previous
        return {
            "restored_state": previous,
            "verification": {"status": "PASS", "profile": profile},
        }

    @staticmethod
    def receipt(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
