"""Discoverable capability registry with progressive detail loading."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .errors import CoreError


KINDS = {"tool", "skill", "mcp"}


@dataclass(frozen=True)
class Capability:
    capability_id: str
    kind: str
    summary: str
    details: dict[str, Any]


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability] | None = None):
        self._items: dict[str, Capability] = {}
        for capability in capabilities or []:
            self.register(capability)

    def register(self, capability: Capability) -> None:
        if (
            not capability.capability_id
            or capability.kind not in KINDS
            or not capability.summary
            or capability.capability_id in self._items
        ):
            raise CoreError("CAPABILITY_INVALID", "Capability registration is invalid.")
        self._items[capability.capability_id] = capability

    def discover(self, kind: str, query: str = "") -> dict:
        if kind not in KINDS:
            raise CoreError("CAPABILITY_KIND_INVALID", "Capability kind is invalid.")
        needle = query.casefold().strip()
        rows = [
            capability
            for capability in self._items.values()
            if capability.kind == kind
            and (
                not needle
                or needle in capability.capability_id.casefold()
                or needle in capability.summary.casefold()
            )
        ]
        items = [
            {
                "id": capability.capability_id,
                "summary": capability.summary,
                "details_deferred": True,
            }
            for capability in sorted(rows, key=lambda item: item.capability_id)
        ]
        return {"items": items, "deferred_count": len(items)}

    def load(self, capability_id: str, kind: str) -> dict:
        capability = self._items.get(capability_id)
        if capability is None or capability.kind != kind:
            raise CoreError("CAPABILITY_NOT_FOUND", "Capability is not registered.")
        encoded = json.dumps(capability.details, sort_keys=True, separators=(",", ":")).encode()
        return {
            "id": capability.capability_id,
            "details": dict(capability.details),
            "details_sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def readiness(self) -> dict[str, bool]:
        return {kind: any(item.kind == kind for item in self._items.values()) for kind in sorted(KINDS)}
