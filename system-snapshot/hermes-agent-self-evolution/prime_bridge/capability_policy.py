"""Closed public capability policy and secret-safe canonicalization."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any


class CapabilityPolicyError(RuntimeError):
    pass


NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
FORBIDDEN_KEY = re.compile(r"(?:api.?key|authorization|credential|password|secret|token)", re.IGNORECASE)
CREDENTIAL_SHAPE = re.compile(
    r"(?:-----BEGIN [^-]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}\b|\bAKIA[0-9A-Z]{16}\b)"
)


@dataclasses.dataclass(frozen=True)
class CapabilityDecision:
    alias: str
    classification: str
    approval: str
    server: str
    tool: str


@dataclasses.dataclass(frozen=True)
class CanonicalArguments:
    digest: str
    keys: tuple[str, ...]
    encoded: bytes = dataclasses.field(repr=False, compare=False)

    def value(self) -> dict[str, Any]:
        return json.loads(self.encoded)


@dataclasses.dataclass(frozen=True)
class CapabilityPolicy:
    enabled: bool
    aliases: dict[str, CapabilityDecision]
    approval_ttl_seconds: int
    max_argument_bytes: int
    max_result_bytes: int

    @classmethod
    def load(cls, path: Path) -> CapabilityPolicy:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if set(raw) != {
            "schema_version", "enabled", "approval_ttl_seconds",
            "max_argument_bytes", "max_result_bytes", "aliases",
        } or raw["schema_version"] != "1.0" or type(raw["enabled"]) is not bool:
            raise CapabilityPolicyError("invalid closed capability policy")
        limits = tuple(raw[key] for key in ("approval_ttl_seconds", "max_argument_bytes", "max_result_bytes"))
        if any(type(value) is not int or value < 1 for value in limits):
            raise CapabilityPolicyError("invalid capability policy limits")
        aliases: dict[str, CapabilityDecision] = {}
        if not isinstance(raw["aliases"], dict) or not raw["aliases"]:
            raise CapabilityPolicyError("capability aliases are required")
        for alias, value in raw["aliases"].items():
            if not NAME.fullmatch(alias) or not isinstance(value, dict) or set(value) != {
                "classification", "approval", "server", "tool",
            }:
                raise CapabilityPolicyError("invalid capability alias")
            if value["classification"] not in {"retrieval", "action", "learning"}:
                raise CapabilityPolicyError("invalid capability classification")
            if value["approval"] not in {"auto", "required"}:
                raise CapabilityPolicyError("invalid capability approval mode")
            if value["classification"] == "retrieval" and value["approval"] != "auto":
                raise CapabilityPolicyError("retrieval must be auto-approved")
            if not TARGET.fullmatch(value["server"]) or not TARGET.fullmatch(value["tool"]):
                raise CapabilityPolicyError("invalid capability target")
            aliases[alias] = CapabilityDecision(alias=alias, **value)
        return cls(raw["enabled"], aliases, *limits)

    def decision(self, alias: str) -> CapabilityDecision:
        if not self.enabled:
            raise CapabilityPolicyError("capabilities are disabled")
        try:
            return self.aliases[alias]
        except (KeyError, TypeError) as exc:
            raise CapabilityPolicyError("capability is denied") from exc

    def public_catalog(self) -> dict[str, dict[str, str]]:
        return {
            alias: {"classification": item.classification, "approval": item.approval}
            for alias, item in sorted(self.aliases.items())
        }

    def canonical_arguments(self, arguments: dict[str, Any]) -> CanonicalArguments:
        if not isinstance(arguments, dict) or any(not isinstance(key, str) for key in arguments):
            raise CapabilityPolicyError("capability arguments must be an object")
        if any(FORBIDDEN_KEY.search(key) for key in arguments):
            raise CapabilityPolicyError("secret-shaped capability argument is forbidden")
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if len(encoded) > self.max_argument_bytes:
            raise CapabilityPolicyError("capability arguments exceed the bounded limit")
        text = encoded.decode("utf-8")
        if CREDENTIAL_SHAPE.search(text):
            raise CapabilityPolicyError("secret-shaped capability argument is forbidden")
        return CanonicalArguments(
            digest=hashlib.sha256(encoded).hexdigest(),
            keys=tuple(sorted(arguments)),
            encoded=encoded,
        )

    def sanitize_result(self, result: Any) -> Any:
        def clean(value: Any, key: str | None = None) -> Any:
            if key is not None and FORBIDDEN_KEY.search(key):
                return "[redacted]"
            if isinstance(value, dict):
                return {str(child_key): clean(child, str(child_key)) for child_key, child in value.items()}
            if isinstance(value, list):
                return [clean(child) for child in value]
            if isinstance(value, str) and CREDENTIAL_SHAPE.search(value):
                return "[redacted]"
            return value
        sanitized = clean(result)
        encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if len(encoded) > self.max_result_bytes:
            return {"status": "truncated", "bytes": len(encoded)}
        return sanitized
