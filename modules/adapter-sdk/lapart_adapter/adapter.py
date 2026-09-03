"""Minimal translation interface implemented by a harness adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .manifest import HarnessManifest


SESSION_STATES = {"new", "active", "detached", "closed", "failed"}


@dataclass(frozen=True)
class AdapterRequest:
    operation: str
    payload: dict[str, Any]
    session_id: str
    session_state: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation, str)
            or not self.operation
            or not isinstance(self.payload, dict)
            or not isinstance(self.session_id, str)
            or not self.session_id
            or self.session_state not in SESSION_STATES
        ):
            raise ValueError("adapter request is invalid")


class HarnessAdapter(ABC):
    """Translation-only surface; all orchestration decisions remain in the core."""

    def __init__(self, manifest: HarnessManifest):
        self.manifest = manifest

    @abstractmethod
    def initialize(self, config_reference: str, data_reference: str) -> dict[str, Any]:
        """Initialize only harness-local translation state from portable references."""

    @abstractmethod
    def shutdown(self, reason: str) -> dict[str, Any]:
        """Shut down only harness-local translation state."""

    @abstractmethod
    def translate_request(self, local_event: Any) -> AdapterRequest:
        """Translate one harness-local event into a shared operation request."""

    @abstractmethod
    def translate_response(self, envelope: dict[str, Any]) -> Any:
        """Translate one shared response envelope into the harness-local form."""

    @abstractmethod
    def readiness_signal(self) -> dict[str, Any]:
        """Report harness-local readiness without replacing core health policy."""

    @abstractmethod
    def rollback_signal(self) -> dict[str, Any]:
        """Report the harness-local rollback reference and state only."""
