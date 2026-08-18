"""Pure, explainable recommendations for optional Prime and OMP delegation.

These receipts are deliberately advisory: the existing Prime route gates and
OMP approval policy remain the only mechanisms that can start either worker.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


_ADVISORY_REQUEST = re.compile(
    r"\b(?:if\s+(?:prime(?:\s+agent)?\s+is\s+)?(?:useful|appropriate|worthwhile)|"
    r"(?:use|delegate\s+to)\s+prime\s+if\s+(?:useful|appropriate|worthwhile))\b",
    re.IGNORECASE,
)


def advisory_requested(message: str) -> bool:
    """Whether the user delegated the choice of worker to Hermes."""
    return isinstance(message, str) and _ADVISORY_REQUEST.search(message) is not None


def prime_advisory(route: Mapping[str, Any], *, requested: bool) -> dict[str, Any]:
    """Explain whether the already-classified task merits Prime delegation."""
    outcome = route.get("outcome")
    family = route.get("family")
    if outcome in {"PRIME_AUTO", "PRIME_CODE"}:
        return {
            "requested": requested,
            "recommendation": "DELEGATE_TO_PRIME",
            "reason": "bounded_operational_work",
            "family": family,
        }
    return {
        "requested": requested,
        "recommendation": "KEEP_NATIVE",
        "reason": route.get("reason", "no_bounded_operational_work"),
        "family": family,
    }


def omp_advisory(route: Mapping[str, Any]) -> dict[str, Any]:
    """Recommend, but never launch, OhMyPi for a delegated Prime job."""
    if route.get("outcome") == "PRIME_CODE":
        return {
            "recommendation": "CONSIDER_OHMYPI",
            "reason": "project_scoped_code_work",
            "approval_required": True,
            "auto_start": False,
        }
    return {
        "recommendation": "KEEP_PRIME_NATIVE",
        "reason": "not_project_scoped_code_work",
        "approval_required": True,
        "auto_start": False,
    }
