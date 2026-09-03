"""Protocol negotiation shared by the installer and future harness adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


SUPPORTED_PROTOCOLS = (1, 2)


@dataclass(frozen=True)
class IncompatibleProtocolError(ValueError):
    local_supported: tuple[int, ...]
    remote_supported: tuple[int, ...]
    code: str = "INCOMPATIBLE_PROTOCOL"

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": "No mutually supported orchestration protocol version.",
            "local_supported": list(self.local_supported),
            "remote_supported": list(self.remote_supported),
        }


def _normalize(versions: Iterable[int]) -> tuple[int, ...]:
    values = tuple(sorted(set(versions)))
    if not values or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in values
    ):
        raise ValueError("protocol versions must be positive integers")
    return values


def negotiate_protocol(
    remote_supported: Iterable[int],
    local_supported: Iterable[int] = SUPPORTED_PROTOCOLS,
) -> int:
    local = _normalize(local_supported)
    remote = _normalize(remote_supported)
    mutual = set(local).intersection(remote)
    if not mutual:
        raise IncompatibleProtocolError(local, remote)
    return max(mutual)
