"""Artifact-bound conjunctive compatibility gate for Hermes/Prime updates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from prime_bridge.rollout_policy import RolloutPolicy


class UpgradeGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    source_commit: str
    image_digest: str
    command_fingerprint: str
    prime_protocol: str
    bridge_version: str

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> Candidate:
        keys = {"source_commit", "image_digest", "command_fingerprint", "prime_protocol", "bridge_version"}
        if not isinstance(value, Mapping) or set(value) != keys or not all(isinstance(value[key], str) for key in keys):
            raise UpgradeGateError("candidate manifest is not closed")
        candidate = cls(**value)
        if not re.fullmatch(r"[0-9a-f]{40}", candidate.source_commit):
            raise UpgradeGateError("invalid candidate source commit")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate.image_digest):
            raise UpgradeGateError("invalid candidate image digest")
        if not re.fullmatch(r"[0-9a-f]{64}", candidate.command_fingerprint):
            raise UpgradeGateError("invalid candidate command fingerprint")
        if not candidate.prime_protocol or not candidate.bridge_version:
            raise UpgradeGateError("candidate protocol metadata is required")
        return candidate

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


class UpdateGate:
    def __init__(self, policy: RolloutPolicy):
        self.policy = policy

    def evaluate(
        self, candidate_value: Mapping[str, Any], checks: Mapping[str, Any], *,
        native_status: str, profile_coverage_percent: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        candidate = Candidate.parse(candidate_value)
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        reasons: list[str] = []
        if not self.policy.enabled or self.policy.stage == "disabled":
            reasons.append("rollout-disabled")
        if set(checks) != self.policy.required_update_checks:
            reasons.append("compatibility-check-set-mismatch")
        else:
            for name in sorted(self.policy.required_update_checks):
                evidence = checks[name]
                if not isinstance(evidence, Mapping) or set(evidence) != {"status", "candidate_fingerprint", "checked_at"}:
                    reasons.append(f"{name}:invalid-evidence")
                    continue
                if evidence["status"] != "PASS":
                    reasons.append(f"{name}:failed")
                if evidence["candidate_fingerprint"] != candidate.fingerprint:
                    reasons.append(f"{name}:candidate-mismatch")
                try:
                    checked = _time(str(evidence["checked_at"]))
                except (TypeError, ValueError):
                    reasons.append(f"{name}:invalid-time")
                else:
                    age = (instant - checked).total_seconds()
                    if age < -60 or age > self.policy.compatibility_evidence_max_age_seconds:
                        reasons.append(f"{name}:stale")
        if native_status != "PASS":
            reasons.append("native-hermes-unhealthy")
        if type(profile_coverage_percent) is not int or profile_coverage_percent < self.policy.minimum_eligible_coverage_percent:
            reasons.append("profile-coverage-insufficient")
        public = {
            "candidate_fingerprint": candidate.fingerprint,
            "source_commit": candidate.source_commit,
            "image_digest": candidate.image_digest,
            "prime_protocol": candidate.prime_protocol,
            "bridge_version": candidate.bridge_version,
            "stage": self.policy.stage,
        }
        decision_fingerprint = hashlib.sha256(json.dumps({
            **public, "status": "READY" if not reasons else "BLOCKED", "reasons": sorted(set(reasons)),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {
            **public,
            "status": "READY" if not reasons else "BLOCKED",
            "reasons": sorted(set(reasons)),
            "decision_fingerprint": decision_fingerprint,
            "native_fallback": True,
        }
