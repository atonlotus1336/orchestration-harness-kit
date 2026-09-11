"""Bounded facts collection for adaptive review.

Wave 0 task 2: bounded facts collection. Facts are key/value observations
collected up to a hard bound; exceeding the bound fails closed rather than
silently truncating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


class FactsLimitError(ValueError):
    """Raised when a fact collection exceeds its configured bound."""


@dataclass(frozen=True)
class Fact:
    """A single key/value observation."""

    key: str
    value: str


@dataclass
class FactCollector:
    """Collects facts up to a hard bound, failing closed when exceeded."""

    max_facts: int
    facts: list[Fact] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_facts < 0:
            raise FactsLimitError("max_facts must be non-negative")

    def add(self, key: str, value: str) -> None:
        if len(self.facts) >= self.max_facts:
            raise FactsLimitError(
                f"fact bound exceeded: {len(self.facts)} >= {self.max_facts}"
            )
        self.facts.append(Fact(key=key, value=value))

    def add_many(self, items: Iterable[tuple[str, str]]) -> None:
        for key, value in items:
            self.add(key, value)

    def as_mapping(self) -> dict[str, str]:
        return {fact.key: fact.value for fact in self.facts}

    def __len__(self) -> int:
        return len(self.facts)
