"""Pure detection of explicit user intent for the autonomous-local profile."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any


_OPERATIVE_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("keep_going_autonomously", re.compile(
        r"\bkeep\s+going\s+autonomously\b", re.IGNORECASE,
    )),
    ("run_autonomously", re.compile(
        r"\b(?:run|execute|start|implement|build|fix|repair)\b"
        r"[^.!?\n]{0,64}\bautonom(?:ous|ously)\b",
        re.IGNORECASE,
    )),
    ("run_unattended", re.compile(
        r"\b(?:run|execute|start|complete|handle|finish)\b[^.!?\n]{0,48}\bunattended\b",
        re.IGNORECASE,
    )),
    ("continue_without_stopping", re.compile(
        r"\b(?:continue|keep|let\s+(?:it|the\s+agent|you)\s+(?:continue|run|work))\b"
        r"[^.!?\n]{0,64}\bwithout\s+(?:stopping|interruption|asking)\b",
        re.IGNORECASE,
    )),
    ("complete_until_done", re.compile(
        r"\b(?:complete|finish|handle|run|work(?:\s+through)?)\b[^.!?\n]{0,64}"
        r"\b(?:until\s+(?:done|complete)|end[- ]to[- ]end)\b",
        re.IGNORECASE,
    )),
    ("autonomous_workflow", re.compile(
        r"\b(?:start|run|execute|complete|handle|implement|build|fix|repair)\b[^.!?\n]{0,64}"
        r"\b(?:autonomous|unattended)\s+(?:run|workflow|execution)\b",
        re.IGNORECASE,
    )),
    ("autonomous_completion_without_stops", re.compile(
        r"\b(?:allow|let|have|keep)\b[^.!?\n]{0,64}"
        r"\b(?:autonomous|unattended|it|the\s+agent)\b[^.!?\n]{0,64}"
        r"\b(?:complete|run|continue|finish)\b[^.!?\n]{0,48}"
        r"\b(?:without\s+(?:intermediate\s+)?stops?|until\s+(?:done|complete))\b",
        re.IGNORECASE,
    )),
)

_DISCUSSION_PREFIX = re.compile(
    r"^(?:(?:please|could\s+you|can\s+you|would\s+you)\s+)?"
    r"(?:describe|explain|define|what\b|how\b|why\b|when\b|should\b|whether\b|plan\s+for|"
    r"document|compare|analy[sz]e|review)\b",
    re.IGNORECASE,
)


def _operative_text(message: str) -> str:
    """Remove content that is not an operative user instruction."""
    text = re.sub(r"```.*?```", " ", message, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*>.*$", " ", text)
    # Quoted examples are explanatory content, not authorization.
    text = re.sub(r"(?:\"[^\"]*\"|'[^']*'|“[^”]*”|‘[^’]*’)", " ", text)
    return " ".join(text.casefold().strip().split())


def detect_autonomous_local_intent(message: str) -> dict[str, Any]:
    """Return a stable, explainable profile decision without side effects.

    Only an operative request from the user activates the profile. Discussion,
    documentation, quoted examples, and ambiguous mentions remain conservative.
    """
    if os.environ.get("LAPART_AUTONOMOUS_LOCAL_MODE", "on").casefold() in {"off", "disabled", "false", "0"}:
        return {
            "activated": False,
            "profile": "conservative",
            "signal": None,
            "reason": "OPERATOR_AUTONOMOUS_LOCAL_DISABLED",
        }
    if not isinstance(message, str) or not message.strip():
        return {
            "activated": False,
            "profile": "conservative",
            "signal": None,
            "reason": "NO_OPERATIVE_AUTONOMOUS_INTENT",
        }
    text = _operative_text(message)
    negated = re.search(
        r"\b(?:do\s+not|don't|never|without)\s+(?:run|execute|start|work|continue|keep)\b"
        r"[^.!?]{0,64}\b(?:autonom(?:ous|ously)|unattended|without\s+stopping)\b",
        message.casefold(),
    )
    if not text or _DISCUSSION_PREFIX.match(text) or negated:
        return {
            "activated": False,
            "profile": "conservative",
            "signal": None,
            "reason": "AUTONOMY_MENTION_IS_DISCUSSION",
        }
    for signal, pattern in _OPERATIVE_SIGNALS:
        if pattern.search(text):
            return {
                "activated": True,
                "profile": "autonomous-local",
                "signal": signal,
                "signal_fingerprint": hashlib.sha256(signal.encode()).hexdigest(),
                "reason": "EXPLICIT_OPERATIVE_AUTONOMOUS_INTENT",
            }
    return {
        "activated": False,
        "profile": "conservative",
        "signal": None,
        "reason": "NO_OPERATIVE_AUTONOMOUS_INTENT",
    }
