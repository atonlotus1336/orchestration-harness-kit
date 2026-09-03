"""Closed, immutable policy loading for the shared orchestration core."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .errors import CoreError


POLICY_KEYS = {
    "schema",
    "protocol_versions",
    "routing_policy",
    "t2_actual_lane",
    "subscription_first",
    "pay_per_token_allowed",
    "external_effects_require_approval",
    "production_activation_allowed",
    "required_promotion_gates",
    "context",
    "tool_projection",
    "activation_states",
}
TIERS = {"atomic", "standard", "complex", "high_risk"}


@dataclass(frozen=True)
class TierPolicy:
    maximum_bytes: int
    maximum_provider_calls: int


@dataclass(frozen=True)
class ToolProjectionPolicy:
    read_only_signals: frozenset[str]
    mutation_signals: frozenset[str]
    safe_tools: frozenset[str]
    discovery_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class CorePolicy:
    protocol_versions: tuple[int, ...]
    routing_policy: str
    t2_actual_lane: str
    subscription_first: bool
    pay_per_token_allowed: bool
    external_effects_require_approval: bool
    production_activation_allowed: bool
    required_promotion_gates: frozenset[str]
    tiers: Mapping[str, TierPolicy]
    required_context_components: frozenset[str]
    maximum_growth_percent: float
    tool_projection: ToolProjectionPolicy
    activation_states: tuple[str, ...]
    fingerprint: str

    @classmethod
    def load(cls, path: Path) -> "CorePolicy":
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CoreError("POLICY_UNAVAILABLE", "Core policy is unavailable.") from exc
        if not isinstance(value, dict) or set(value) != POLICY_KEYS:
            raise CoreError("POLICY_INVALID", "Core policy keys are not closed.")
        if value.get("schema") != "lapart.core-policy.v2":
            raise CoreError("POLICY_INVALID", "Core policy schema is unsupported.")
        if value.get("protocol_versions") != [1, 2]:
            raise CoreError("POLICY_INVALID", "Protocol versions must preserve v1 and v2.")
        if value.get("routing_policy") != "gated" or value.get("t2_actual_lane") != "T3":
            raise CoreError("POLICY_INVALID", "Gated routing must map T2 to T3.")
        boolean_fields = {
            "subscription_first": True,
            "pay_per_token_allowed": False,
            "external_effects_require_approval": True,
            "production_activation_allowed": False,
        }
        if any(value.get(name) is not expected for name, expected in boolean_fields.items()):
            raise CoreError("POLICY_INVALID", "Core safety booleans changed.")
        required_gates = value.get("required_promotion_gates")
        if set(required_gates or []) != {"quality", "calls", "latency", "rollback", "tokens"}:
            raise CoreError("POLICY_INVALID", "Promotion gates are incomplete.")
        context = value.get("context")
        if not isinstance(context, dict) or set(context) != {
            "maximum_growth_percent",
            "tiers",
            "required_components",
        }:
            raise CoreError("POLICY_INVALID", "Context policy keys are invalid.")
        growth = context.get("maximum_growth_percent")
        if not isinstance(growth, (int, float)) or isinstance(growth, bool) or not 0 <= growth <= 100:
            raise CoreError("POLICY_INVALID", "Context growth threshold is invalid.")
        tiers = context.get("tiers")
        if not isinstance(tiers, dict) or set(tiers) != TIERS:
            raise CoreError("POLICY_INVALID", "Context tiers are incomplete.")
        normalized: dict[str, TierPolicy] = {}
        for name, row in tiers.items():
            if not isinstance(row, dict) or set(row) != {"maximum_bytes", "maximum_provider_calls"}:
                raise CoreError("POLICY_INVALID", f"Tier {name} keys are invalid.")
            if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in row.values()):
                raise CoreError("POLICY_INVALID", f"Tier {name} limits are invalid.")
            normalized[name] = TierPolicy(**row)
        required_components = context.get("required_components")
        if set(required_components or []) != {"system_prompt", "authority_context"}:
            raise CoreError("POLICY_INVALID", "Required context components changed.")
        projection = value.get("tool_projection")
        projection_keys = {"read_only_signals", "mutation_signals", "safe_tools", "discovery_prefixes"}
        if not isinstance(projection, dict) or set(projection) != projection_keys:
            raise CoreError("POLICY_INVALID", "Tool projection policy keys are invalid.")
        for key in projection_keys:
            rows = projection[key]
            if not isinstance(rows, list) or not rows or len(rows) != len(set(rows)) or any(not isinstance(row, str) or not row for row in rows):
                raise CoreError("POLICY_INVALID", f"Tool projection {key} is invalid.")
        if set(projection["safe_tools"]) != {"read", "grep", "find", "glob", "ls"}:
            raise CoreError("POLICY_INVALID", "Read-only tool safety baseline changed.")
        if set(projection["discovery_prefixes"]) != {"mcp", "skill", "search", "tool_search"}:
            raise CoreError("POLICY_INVALID", "Capability discovery projection changed.")
        states = value.get("activation_states")
        if states != ["inactive", "shadow", "review", "production"]:
            raise CoreError("POLICY_INVALID", "Activation states changed.")
        return cls(
            protocol_versions=(1, 2),
            routing_policy="gated",
            t2_actual_lane="T3",
            subscription_first=True,
            pay_per_token_allowed=False,
            external_effects_require_approval=True,
            production_activation_allowed=False,
            required_promotion_gates=frozenset(required_gates),
            tiers=MappingProxyType(normalized),
            required_context_components=frozenset(required_components),
            maximum_growth_percent=float(growth),
            tool_projection=ToolProjectionPolicy(
                read_only_signals=frozenset(projection["read_only_signals"]),
                mutation_signals=frozenset(projection["mutation_signals"]),
                safe_tools=frozenset(projection["safe_tools"]),
                discovery_prefixes=tuple(projection["discovery_prefixes"]),
            ),
            activation_states=tuple(states),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        )
