"""Deterministic context selection with explicit byte attribution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

from .errors import CoreError
from .policy import CorePolicy


CATEGORIES = {
    "system_prompt",
    "authority_context",
    "skills_index",
    "tool_schema",
    "selected_content",
}


@dataclass(frozen=True)
class ContextItem:
    item_id: str
    category: str
    content: str
    required: bool = False
    priority: int = 0

    @property
    def byte_count(self) -> int:
        return len(self.content.encode("utf-8"))


def _item(value: dict) -> ContextItem:
    if set(value) != {"id", "category", "content", "required", "priority"}:
        raise CoreError("CONTEXT_ITEM_INVALID", "Context item fields are not closed.")
    if (
        not isinstance(value["id"], str)
        or not value["id"]
        or value["category"] not in CATEGORIES
        or not isinstance(value["content"], str)
        or type(value["required"]) is not bool
        or type(value["priority"]) is not int
    ):
        raise CoreError("CONTEXT_ITEM_INVALID", "Context item values are invalid.")
    return ContextItem(
        item_id=value["id"],
        category=value["category"],
        content=value["content"],
        required=value["required"],
        priority=value["priority"],
    )


def select_context(items: Iterable[dict], task_tier: str, policy: CorePolicy) -> dict:
    if task_tier not in policy.tiers:
        raise CoreError("INVALID_TASK_TIER", "Context tier is not supported.")
    normalized = tuple(_item(value) for value in items)
    if len({item.item_id for item in normalized}) != len(normalized):
        raise CoreError("CONTEXT_ITEM_INVALID", "Context item IDs must be unique.")
    for category in policy.required_context_components:
        rows = [item for item in normalized if item.category == category]
        if len(rows) != 1 or rows[0].required is not True:
            raise CoreError(
                "REQUIRED_CONTEXT_MISSING",
                "Required authority or system context is missing.",
                details={"category": category},
            )

    maximum = policy.tiers[task_tier].maximum_bytes
    required = sorted(
        (item for item in normalized if item.required),
        key=lambda item: (-item.priority, item.item_id),
    )
    required_bytes = sum(item.byte_count for item in required)
    if required_bytes > maximum:
        raise CoreError(
            "REQUIRED_CONTEXT_OVER_BUDGET",
            "Required context exceeds the approved tier budget.",
            details={"required_bytes": required_bytes, "maximum_bytes": maximum},
        )
    selected = list(required)
    used = required_bytes
    optional = sorted(
        (item for item in normalized if not item.required),
        key=lambda item: (-item.priority, item.item_id),
    )
    deferred: list[str] = []
    for item in optional:
        if used + item.byte_count <= maximum:
            selected.append(item)
            used += item.byte_count
        else:
            deferred.append(item.item_id)

    category_bytes = {category: 0 for category in CATEGORIES}
    for item in selected:
        category_bytes[item.category] += item.byte_count
    total_available = sum(item.byte_count for item in normalized)
    attribution = {
        "system_prompt_bytes": category_bytes["system_prompt"],
        "authority_context_bytes": category_bytes["authority_context"],
        "skills_index_bytes": category_bytes["skills_index"],
        "tool_schema_bytes": category_bytes["tool_schema"],
        "selected_content_bytes": category_bytes["selected_content"],
        "rendered_total_bytes": used,
        "saved_bytes": total_available - used,
    }
    canonical = json.dumps(
        {
            "selected": [item.item_id for item in selected],
            "deferred": deferred,
            "attribution": attribution,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "selection": [item.item_id for item in selected],
        "deferred": deferred,
        "attribution": attribution,
        "selection_sha256": hashlib.sha256(canonical).hexdigest(),
    }
