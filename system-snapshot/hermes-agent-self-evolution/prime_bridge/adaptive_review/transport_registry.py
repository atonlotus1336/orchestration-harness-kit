
"""Sealed built-in review transport admission."""
from __future__ import annotations

from .herdr_transport import HerdrReviewTransport
from .transport import JsonReviewTransport

BUILT_IN_REPORT_ONLY_TRANSPORT_TYPES = (
    JsonReviewTransport,
    HerdrReviewTransport,
)


def is_admitted_report_only_transport(value: object) -> bool:
    return type(value) in BUILT_IN_REPORT_ONLY_TRANSPORT_TYPES


__all__ = [
    "BUILT_IN_REPORT_ONLY_TRANSPORT_TYPES",
    "is_admitted_report_only_transport",
]
