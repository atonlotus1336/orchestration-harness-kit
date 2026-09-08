"""Frozen reviewer catalog with deterministic trigger and overlap handling."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .adapters import ReviewerCapability


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Reviewer:
    reviewer_id: str
    lens: str
    triggers: tuple[str, ...]
    skill_path: str
    skill_sha256: str
    overlaps: tuple[str, ...] = ()
    requires_confirmation: bool = False
    required: bool = True


@dataclass
class Catalog:
    reviewers: dict[str, Reviewer] = field(default_factory=dict)
    capabilities: dict[str, ReviewerCapability] = field(default_factory=dict)

    def register(self, reviewer: Reviewer, capability: ReviewerCapability) -> None:
        if reviewer.reviewer_id in self.reviewers:
            raise CatalogError(f"duplicate reviewer: {reviewer.reviewer_id}")
        self.reviewers[reviewer.reviewer_id] = reviewer
        self.capabilities[reviewer.reviewer_id] = capability

    def triggered(self, triggers: Iterable[str]) -> tuple[Reviewer, ...]:
        requested = set(triggers)
        candidates = sorted(
            (item for item in self.reviewers.values() if requested.intersection(item.triggers)),
            key=lambda item: item.reviewer_id,
        )
        selected: list[Reviewer] = []
        selected_ids: set[str] = set()
        for reviewer in candidates:
            if selected_ids.intersection(reviewer.overlaps):
                continue
            selected.append(reviewer)
            selected_ids.add(reviewer.reviewer_id)
        return tuple(selected)

    def verify_skill(self, reviewer_id: str) -> bool:
        reviewer = self.reviewers[reviewer_id]
        path = Path(reviewer.skill_path)
        if not path.is_file() or len(reviewer.skill_sha256) != 64:
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == reviewer.skill_sha256

    def capability_for(self, reviewer_id: str) -> ReviewerCapability:
        try:
            return self.capabilities[reviewer_id]
        except KeyError as exc:
            raise CatalogError(f"missing capability: {reviewer_id}") from exc


def load_frozen_catalog(
    config_path: Path,
    *,
    vault_root: Path,
    readable_files: Iterable[str],
) -> Catalog:
    """Load the canonical reviewer catalog and pin every skill by content hash."""
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0" or not isinstance(raw.get("reviewers"), list):
        raise CatalogError("unsupported or malformed reviewer catalog")
    capability = ReviewerCapability(tuple(readable_files))
    catalog = Catalog()
    for item in raw["reviewers"]:
        relative = Path(item["skill_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise CatalogError(f"unsafe skill path: {relative}")
        reviewer = Reviewer(
            reviewer_id=item["reviewer_id"],
            lens=item["lens"],
            triggers=tuple(item["triggers"]),
            skill_path=str((vault_root / relative).resolve()),
            skill_sha256=item["skill_sha256"],
            overlaps=tuple(item.get("overlaps", ())),
            requires_confirmation=bool(item.get("requires_confirmation", False)),
            required=bool(item.get("required", True)),
        )
        catalog.register(reviewer, capability)
    if not catalog.reviewers:
        raise CatalogError("empty reviewer catalog")
    return catalog
