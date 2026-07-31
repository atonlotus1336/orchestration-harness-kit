"""M2 capture-quality helpers shared by producer and parser."""

from __future__ import annotations

import re

_NOISE_LINE_RE = re.compile(r"^\s*(---|#|\[|\||>)")
_KNOWN_SCAFFOLD_RE = re.compile(
    r"(append-only log|this file tracks decisions|irreversible choices and cross-project learnings|"
    r"format:\s*`\[yyyy-mm-dd\]|project-scoped decisions live)",
    re.IGNORECASE,
)


def is_standalone_noise_line(line: str) -> bool:
    """Return True for markdown scaffolding that must never become a memory."""
    stripped = (line or "").strip()
    if not stripped:
        return True
    return bool(_NOISE_LINE_RE.match(stripped) or _KNOWN_SCAFFOLD_RE.search(stripped))


def clean_memory_line(line: str) -> str:
    """Normalize a candidate one-line memory while preserving its wording."""
    return " ".join((line or "").strip().split())
