"""Secret-safe output helpers used by installer commands."""

from __future__ import annotations

import re

_RULES = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(?:api[_-]?key|token|password|secret)\s*[=:]\s*[^\s,;]+"), "credential=<redacted>"),
    (re.compile(r"(?i)https?://[^/@\s:]+:[^/@\s]+@"), "https://<redacted>@"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<redacted-email>"),
]


def redact(value: object) -> str:
    text = str(value)
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
