"""Bounded adapter from a gateway-approved route to the existing Prime session API."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from prime_bridge.intent_router import IntentPolicy, classify


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PONYTAIL = ROOT.parent / "skills-shared/ponytail/skills/ponytail/SKILL.md"


def ponytail_snapshot(path: Path = CANONICAL_PONYTAIL) -> dict[str, Any]:
    """Read the one canonical skill; callers decide whether its absence is a fallback."""
    try:
        data = path.read_bytes()
    except OSError:
        return {"available": False}
    return {
        "available": True,
        "path": "skills-shared/ponytail/skills/ponytail/SKILL.md",
        "intensity": "full",
        "fingerprint": hashlib.sha256(data).hexdigest(),
        "content": data.decode("utf-8"),
    }


async def auto_handoff(
    service: Any, *, message: str, profile: str, mode: str, session_id: str,
    workspace: str, project: str, readiness: bool, compatibility: bool, admitted: bool,
    policy: IntentPolicy,
) -> dict[str, Any]:
    """Start an existing Prime session only after every pure gate has passed."""
    ponytail = ponytail_snapshot()
    decision = classify(
        message, profile=profile, mode=mode, readiness=readiness,
        compatibility=compatibility, admitted=admitted, ponytail=ponytail, policy=policy,
        # The gateway always operates within a project context (workspace and
        # project are pinned by the caller), so repository code tasks are
        # project-scoped and may route to PRIME_CODE.
        project_scope=True,
    )
    if decision["outcome"] not in {"PRIME_AUTO", "PRIME_CODE"}:
        # An explicit Ponytail invocation that failed a Prime gate hard-blocks
        # with PRIME_REQUIRED_UNAVAILABLE; it must never fall back to native.
        return {"route": decision, "job": None}
    # ponytail: the canonical skill is injected once at the delegated boundary; a
    # separate prompt framework is unnecessary until multiple governed skills exist.
    prompt = f"{ponytail['content']}\n\n## User task\n{message}"
    try:
        job = await service.start_session({
            "prompt": prompt, "workspace": workspace, "project": project,
            "session_id": session_id, "explicit_handoff": True,
        })
    except Exception:
        decision = {**decision, "outcome": "NATIVE", "reason": "PRIME_START_FAILED"}
        return {"route": decision, "job": None}
    return {"route": decision, "job": job}
