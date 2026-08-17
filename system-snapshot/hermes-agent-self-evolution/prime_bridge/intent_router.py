"""Closed, side-effect-free request classification for Prime auto-handoff."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class IntentPolicyError(RuntimeError):
    pass


_PROFILE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FAMILIES = frozenset({
    "gsd_plan", "gsd_execute", "phase_check", "doublecheck", "audit", "debug",
    "diagnose", "bug_fix", "code_task", "ponytail_invocation",
})

# Case-insensitive detector for explicit Ponytail invocation forms. It matches
# only the canonical invocation forms (run/use ponytail, $ponytail, and the
# [$ponytail:ponytail] bracket form); a bare "ponytail" mention in quoted text
# or discussion does not match any of these forms.
_PONYTAIL_INVOCATION_RE = re.compile(
    r"(?:\brun\s+ponytail\b|\buse\s+ponytail\b|\$ponytail\b|\[\$ponytail:ponytail\])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentPolicy:
    enabled: bool
    eligible_modes: frozenset[str]
    excluded_profiles: frozenset[str]
    families: Mapping[str, tuple[str, ...]]
    ponytail_path: str
    ponytail_intensity: str
    ponytail_required: bool
    fingerprint: str

    @classmethod
    def load(cls, path: Path) -> "IntentPolicy":
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise IntentPolicyError("Prime auto-route policy is unavailable") from exc
        expected = {"schema_version", "enabled", "eligible_modes", "excluded_profiles", "families", "ponytail", "native_fallback"}
        if not isinstance(value, dict) or set(value) != expected or value.get("schema_version") != "1.0":
            raise IntentPolicyError("Prime auto-route policy keys are not closed")
        if type(value["enabled"]) is not bool or value["native_fallback"] is not True:
            raise IntentPolicyError("native fallback is mandatory")
        modes = value["eligible_modes"]
        if not isinstance(modes, list) or set(modes) != {"prime-available", "prime-default"}:
            raise IntentPolicyError("eligible modes are invalid")
        excluded = value["excluded_profiles"]
        if not isinstance(excluded, list) or len(excluded) != len(set(excluded)) or any(not isinstance(item, str) or not _PROFILE.fullmatch(item) for item in excluded):
            raise IntentPolicyError("excluded profiles are invalid")
        families = value["families"]
        if not isinstance(families, dict) or set(families) != _FAMILIES:
            raise IntentPolicyError("route families are not closed")
        normalized: dict[str, tuple[str, ...]] = {}
        for name, phrases in families.items():
            if not isinstance(phrases, list) or not phrases or len(phrases) != len(set(phrases)):
                raise IntentPolicyError("route phrases are invalid")
            if any(not isinstance(phrase, str) or phrase != phrase.strip().lower() or not phrase or "*" in phrase for phrase in phrases):
                raise IntentPolicyError("route phrases must be exact lower-case prefixes")
            normalized[name] = tuple(phrases)
        ponytail = value["ponytail"]
        if not isinstance(ponytail, dict) or set(ponytail) != {"canonical_relative_path", "intensity", "required"}:
            raise IntentPolicyError("Ponytail policy is invalid")
        if ponytail["canonical_relative_path"] != "skills-shared/ponytail/skills/ponytail/SKILL.md" or ponytail["intensity"] != "full" or ponytail["required"] is not True:
            raise IntentPolicyError("canonical Ponytail policy is required")
        return cls(
            enabled=value["enabled"], eligible_modes=frozenset(modes), excluded_profiles=frozenset(excluded),
            families=normalized, ponytail_path=ponytail["canonical_relative_path"],
            ponytail_intensity="full", ponytail_required=True, fingerprint=hashlib.sha256(raw).hexdigest(),
        )


_REQUEST_PREFIXES = (
    "please ", "please, ", "well, ", "hey, ",
    "can you ", "could you ", "would you ", "will you ",
    "i need you to ", "i want you to ", "help me ", "help me to ",
)


def _normalized_request(message: str) -> str:
    """Normalize a user request and remove non-semantic polite lead-ins.

    Routing stays prefix-based after this step. That keeps quoted examples and
    explanatory questions native while allowing ordinary requests such as
    ``Could you verify phase 10?`` to reach the governed Prime path.
    """
    text = " ".join(message.casefold().strip().split())
    for _ in range(3):
        prefix = next((item for item in _REQUEST_PREFIXES if text.startswith(item)), None)
        if prefix is None:
            break
        text = text[len(prefix):].lstrip()
    return text


def _decision(*, outcome: str, reason: str, policy: IntentPolicy, family: str | None = None,
              ponytail_fingerprint: str | None = None) -> dict[str, Any]:
    return {
        "outcome": outcome, "reason": reason, "family": family,
        "policy_fingerprint": policy.fingerprint,
        "ponytail": {"required": True, "intensity": policy.ponytail_intensity,
                      "path": policy.ponytail_path, "fingerprint": ponytail_fingerprint},
    }


def classify(message: str, *, profile: str, mode: str, readiness: bool, compatibility: bool,
             admitted: bool, ponytail: Mapping[str, Any], policy: IntentPolicy,
             project_scope: bool = False) -> dict[str, Any]:
    """Return a route candidate without filesystem, network, or worker side effects."""
    if not isinstance(message, str) or not _PROFILE.fullmatch(profile):
        return _decision(outcome="REJECTED", reason="INVALID_REQUEST", policy=policy)
    if not policy.enabled:
        return _decision(outcome="NATIVE", reason="POLICY_DISABLED", policy=policy)
    if profile in policy.excluded_profiles or mode not in policy.eligible_modes:
        return _decision(outcome="NATIVE", reason="PROFILE_NOT_ELIGIBLE", policy=policy)
    text = _normalized_request(message)
    # Detect an explicit Ponytail invocation (case-insensitive, form-based).
    # This runs before the existing families so a Ponytail invocation is always
    # Prime-required, even when it also mentions a code action.
    ponytail_invoked = _PONYTAIL_INVOCATION_RE.search(message) is not None
    code_phrases = policy.families.get("code_task", ())
    code_family = next((name for name, phrases in (("code_task", code_phrases),) if any(text == phrase or text.startswith(phrase + " ") for phrase in phrases)), None)
    if ponytail_invoked:
        family = "ponytail_invocation"
    elif project_scope and code_family is not None:
        # With explicit project scope, repository code actions route to code_task.
        family = "code_task"
    else:
        # Without project scope, preserve the existing Phase 9 families first;
        # a code_task-only phrase (no existing family) falls back to native.
        family = next((name for name, phrases in policy.families.items() if name not in ("code_task", "ponytail_invocation") and any(text == phrase or text.startswith(phrase + " ") for phrase in phrases)), None)
        if family is None and code_family is not None:
            return _decision(outcome="NATIVE", reason="NO_PROJECT_SCOPE", policy=policy, family="code_task")
    if family is None:
        return _decision(outcome="NATIVE", reason="NO_EXPLICIT_OPERATIONAL_INTENT", policy=policy)
    fingerprint = ponytail.get("fingerprint") if isinstance(ponytail, Mapping) else None
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or ponytail.get("available") is not True:
        return _decision(outcome="NATIVE", reason="PONYTAIL_SKILL_UNAVAILABLE", policy=policy, family=family)
    if ponytail.get("path") != policy.ponytail_path or ponytail.get("intensity") != policy.ponytail_intensity:
        return _decision(outcome="NATIVE", reason="PONYTAIL_SKILL_MISMATCH", policy=policy, family=family)
    if not readiness:
        gate_reason = "READINESS_UNVERIFIED"
    elif not compatibility:
        gate_reason = "COMPATIBILITY_UNVERIFIED"
    elif not admitted:
        gate_reason = "ADMISSION_REJECTED"
    else:
        gate_reason = None
    if gate_reason is not None:
        # An explicit Ponytail invocation is Prime-required: a failed Prime gate
        # hard-blocks with PRIME_REQUIRED_UNAVAILABLE instead of native fallback.
        if family == "ponytail_invocation":
            return _decision(outcome="PRIME_REQUIRED_UNAVAILABLE", reason=gate_reason, policy=policy, family=family, ponytail_fingerprint=fingerprint)
        return _decision(outcome="NATIVE", reason=gate_reason, policy=policy, family=family, ponytail_fingerprint=fingerprint)
    if family == "ponytail_invocation":
        # Code-scoped Ponytail (project scope or a code_task phrase) routes to
        # PRIME_CODE; any other explicit Ponytail invocation routes to PRIME_AUTO.
        if project_scope or code_family is not None:
            return _decision(outcome="PRIME_CODE", reason="ELIGIBLE", policy=policy, family=family, ponytail_fingerprint=fingerprint)
        return _decision(outcome="PRIME_AUTO", reason="ELIGIBLE", policy=policy, family=family, ponytail_fingerprint=fingerprint)
    if family == "code_task":
        # Repository-changing code work requires explicit project scope; a
        # general programming question without a repository action stays native.
        if not project_scope:
            return _decision(outcome="NATIVE", reason="NO_PROJECT_SCOPE", policy=policy, family=family, ponytail_fingerprint=fingerprint)
        return _decision(outcome="PRIME_CODE", reason="ELIGIBLE", policy=policy, family=family, ponytail_fingerprint=fingerprint)
    return _decision(outcome="PRIME_AUTO", reason="ELIGIBLE", policy=policy, family=family, ponytail_fingerprint=fingerprint)
