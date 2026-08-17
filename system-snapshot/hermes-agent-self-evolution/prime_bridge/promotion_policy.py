"""Fail-closed policy for four-lane Hermes learning promotion."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PromotionPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromotionDecision:
    action: str
    reason: str


@dataclass(frozen=True)
class PromotionPolicy:
    enabled: bool
    max_content_bytes: int
    default_expiry_days: int
    shared_root: Path
    auto_global_subject_prefixes: tuple[str, ...]
    approval_subject_prefixes: tuple[str, ...]
    configuration_envelopes: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls, path: Path) -> PromotionPolicy:
        value = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "schema_version", "enabled", "max_content_bytes", "default_expiry_days",
            "shared_root", "auto_global_subject_prefixes", "approval_subject_prefixes",
            "configuration_envelopes",
        }
        if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != "1.0":
            raise PromotionPolicyError("learning policy keys are not closed")
        if type(value["enabled"]) is not bool:
            raise PromotionPolicyError("invalid learning policy status")
        if type(value["max_content_bytes"]) is not int or value["max_content_bytes"] < 256:
            raise PromotionPolicyError("invalid learning content bound")
        if type(value["default_expiry_days"]) is not int or not 1 <= value["default_expiry_days"] <= 3650:
            raise PromotionPolicyError("invalid promotion expiry")
        for key in ("auto_global_subject_prefixes", "approval_subject_prefixes"):
            if not isinstance(value[key], list) or not all(isinstance(item, str) and item.endswith(".") for item in value[key]):
                raise PromotionPolicyError("invalid subject prefix policy")
        envelopes = value["configuration_envelopes"]
        if not isinstance(envelopes, list) or any(
            not isinstance(item, dict) or set(item) != {"name", "target", "remote_required"}
            or not isinstance(item["remote_required"], bool)
            for item in envelopes
        ):
            raise PromotionPolicyError("invalid configuration envelopes")
        return cls(
            enabled=value["enabled"], max_content_bytes=value["max_content_bytes"],
            default_expiry_days=value["default_expiry_days"], shared_root=Path(value["shared_root"]),
            auto_global_subject_prefixes=tuple(value["auto_global_subject_prefixes"]),
            approval_subject_prefixes=tuple(value["approval_subject_prefixes"]),
            configuration_envelopes=tuple(copy.deepcopy(envelopes)),
        )

    def envelope(self, name: str) -> dict[str, Any] | None:
        return next((copy.deepcopy(item) for item in self.configuration_envelopes if item["name"] == name), None)

    def decide(self, proposal: dict[str, Any]) -> PromotionDecision:
        if not self.enabled:
            return PromotionDecision("rejected", "learning promotion is disabled")
        if any(proposal["subject"].startswith(prefix) for prefix in self.approval_subject_prefixes):
            return PromotionDecision("approval_required", "protected subject category")
        if proposal["privacy"] == "restricted":
            return PromotionDecision("approval_required", "restricted learning cannot auto-promote")
        validation = proposal.get("validation") or {}
        if proposal["lane"] == "policy":
            return PromotionDecision("approval_required", "policy and safety are operator-owned")
        if validation.get("outcome") != "passed":
            return PromotionDecision("validation_required", "host validation has not passed")
        if proposal["target_scope"] == "global" and not any(
            proposal["subject"].startswith(prefix) for prefix in self.auto_global_subject_prefixes
        ):
            return PromotionDecision("approval_required", "global subject is outside automatic categories")
        if proposal.get("scope_widening") and validation.get("mode") != "independent":
            return PromotionDecision("approval_required", "scope widening requires independent validation")
        if proposal["lane"] == "fact":
            return PromotionDecision("auto_promote", "verified low-risk fact")
        if proposal["lane"] == "procedure":
            if validation.get("mode") not in {"deterministic", "independent"}:
                return PromotionDecision("approval_required", "procedure validation is not reusable")
            return PromotionDecision("auto_promote", "verified reusable procedure")
        if proposal["lane"] == "configuration":
            return PromotionDecision("transaction_required", "configuration requires an exact transaction envelope")
        return PromotionDecision("rejected", "unknown promotion lane")
