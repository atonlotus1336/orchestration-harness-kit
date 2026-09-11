"""Stable value types for adaptive code-review policy and receipts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Scope(str, Enum):
    CHECKPOINT = "checkpoint"
    PLAN = "plan"
    PHASE = "phase"
    SYSTEM = "system"


class Pipeline(str, Enum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"


class Verdict(str, Enum):
    PASS = "PASS"
    FINDINGS = "FINDINGS"
    INCONCLUSIVE = "INCONCLUSIVE"


class RolloutMode(str, Enum):
    SHADOW = "shadow"
    ADVISORY = "advisory"
    ENFORCED_PHASE = "enforced_phase"
    MANUAL_SYSTEM = "manual_system"


@dataclass(frozen=True)
class LoadedPolicy:
    data: Mapping[str, Any]
    fingerprint: str

    @property
    def rollout_mode(self) -> RolloutMode:
        return RolloutMode(self.data["rollout_mode"])
