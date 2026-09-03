"""Deterministic, capability-preserving tool projection owned by the core."""

from __future__ import annotations

import re
from typing import Iterable

from .policy import CorePolicy


def _normalized_tools(values: Iterable[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        selected.append(value)
    return selected


def _is_discovery(name: str, prefixes: tuple[str, ...]) -> bool:
    normalized = name.casefold().replace(":", ".")
    return any(
        normalized == prefix
        or normalized.startswith(prefix + ".")
        or normalized.startswith(prefix + "_")
        for prefix in prefixes
    )


def _is_read_only(name: str, safe_tools: frozenset[str]) -> bool:
    normalized = name.casefold().replace(":", ".")
    return normalized in safe_tools or normalized.rsplit(".", 1)[-1] in safe_tools


def project_tools(request_text: str, available_tools: list[str], policy: CorePolicy) -> dict:
    words = frozenset(re.findall(r"[a-z]+", request_text.casefold()))
    if not request_text.strip() or words.intersection(policy.tool_projection.mutation_signals):
        mode = "full"
        reason = "mutation-or-empty"
    elif words.intersection(policy.tool_projection.read_only_signals):
        mode = "read_only"
        reason = "explicit-read-only"
    else:
        mode = "full"
        reason = "ambiguous"

    tools = _normalized_tools(available_tools)
    if mode == "full":
        selected = tools
    else:
        selected = [
            name for name in tools
            if _is_read_only(name, policy.tool_projection.safe_tools)
            or _is_discovery(name, policy.tool_projection.discovery_prefixes)
        ]
    selected_set = set(selected)
    disabled = [name for name in tools if name not in selected_set]
    discovery = [name for name in tools if _is_discovery(name, policy.tool_projection.discovery_prefixes)]
    return {
        "mode": mode,
        "selected_tools": selected,
        "disabled_tools": disabled,
        "discovery_preserved": all(name in selected_set for name in discovery),
        "reason": reason,
    }
