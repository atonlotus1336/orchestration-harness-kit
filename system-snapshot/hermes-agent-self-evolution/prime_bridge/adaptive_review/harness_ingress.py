"""Harness-neutral, transport-blind ingress for adaptive review requests.

This module is intentionally not wired into the evaluator controller yet.
Normalization grants no permission to run JSON, Herdr, or any other transport.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any, Mapping

from prime_bridge.harness_registry import UNIVERSAL_HARNESS_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADAPTERS = ROOT / "config" / "evaluator-harness-adapters.json"
_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_REQUIRED = frozenset({
    "schema_version", "harness", "goal_id", "receipt_id", "attempt_id",
    "source_identity", "source_hashes", "rubric", "content", "deadline_ms",
    "reserved_budget", "capability_attestation",
})
_BUDGET_KEYS = frozenset({
    "max_context_bytes", "max_model_calls", "max_output_tokens",
    "max_total_tokens", "max_elapsed_seconds",
})
_ATTESTATION_KEYS = frozenset({
    "tool_names", "source_mounted", "patch_channel", "arbitrary_command",
})


class HarnessIngressError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedReviewRequest:
    ingress_harness: str
    packet_sha256: str
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ingress_harness": self.ingress_harness,
            "packet_sha256": self.packet_sha256,
            "payload": dict(self.payload),
        }


def _closed(value: Any, keys: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise HarnessIngressError(f"{label} is not a closed contract")
    return value


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise HarnessIngressError(f"invalid {label}")
    return value


def _safe_content_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise HarnessIngressError("invalid content key")
    posix = PurePosixPath(value)
    if (PureWindowsPath(value).drive or posix.is_absolute() or chr(92) in value
            or ".." in posix.parts or ":" in value):
        raise HarnessIngressError("unsafe content key")
    return str(posix)


def load_adapter_registry(path: Path = DEFAULT_ADAPTERS) -> Mapping[str, Mapping[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise HarnessIngressError("harness adapter registry unreadable") from exc
    if set(data) != {"schema_version", "activation_state", "contract_schema", "result_schema", "adapters", "note"}:
        raise HarnessIngressError("harness adapter registry is not closed")
    if data["schema_version"] != "1.0" or data["activation_state"] != "inactive":
        raise HarnessIngressError("only the inactive adapter registry is admitted before connection")
    adapters = data.get("adapters")
    if not isinstance(adapters, Mapping) or set(adapters) != set(UNIVERSAL_HARNESS_REGISTRY):
        raise HarnessIngressError("adapter registry drifts from canonical harness registry")
    normalized: dict[str, Mapping[str, str]] = {}
    for harness, spec in adapters.items():
        if (not isinstance(spec, Mapping)
                or set(spec) != {"normalized_name", "contract_version"}
                or spec.get("normalized_name") != harness
                or spec.get("contract_version") != "1.0"):
            raise HarnessIngressError("invalid harness adapter declaration")
        normalized[harness] = dict(spec)
    return normalized


def normalize_review_submission(
    submission: Mapping[str, Any], *,
    adapters: Mapping[str, Mapping[str, str]] | None = None,
) -> NormalizedReviewRequest:
    value = _closed(submission, _REQUIRED, "review submission")
    if value.get("schema_version") != "1.0":
        raise HarnessIngressError("unsupported review contract version")

    harness = value.get("harness")
    registry = adapters if adapters is not None else load_adapter_registry()
    if harness not in UNIVERSAL_HARNESS_REGISTRY or harness not in registry:
        raise HarnessIngressError("unsupported harness")

    attestation = _closed(value.get("capability_attestation"), _ATTESTATION_KEYS, "capability attestation")
    if (attestation.get("tool_names") != []
            or attestation.get("source_mounted") is not False
            or attestation.get("patch_channel") is not False
            or attestation.get("arbitrary_command") is not False):
        raise HarnessIngressError("review ingress must be report-only")

    source_hashes = value.get("source_hashes")
    content = value.get("content")
    if not isinstance(source_hashes, Mapping) or not isinstance(content, Mapping):
        raise HarnessIngressError("source hashes and content must be mappings")
    if not 1 <= len(content) <= 200 or set(content) != set(source_hashes):
        raise HarnessIngressError("content and source hash scope mismatch")
    normalized_content: dict[str, str] = {}
    normalized_hashes: dict[str, str] = {}
    for raw_path in sorted(content):
        path = _safe_content_path(raw_path)
        body, digest = content[raw_path], source_hashes[raw_path]
        if not isinstance(body, str) or len(body.encode("utf-8")) > 1_048_576:
            raise HarnessIngressError("content item exceeds contract")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise HarnessIngressError("invalid source hash")
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != digest:
            raise HarnessIngressError("source hash does not match content")
        normalized_content[path] = body
        normalized_hashes[path] = digest

    budget = _closed(value.get("reserved_budget"), _BUDGET_KEYS, "reserved budget")
    if any(type(budget[key]) is not int or budget[key] < 1 for key in _BUDGET_KEYS):
        raise HarnessIngressError("invalid reserved budget")
    deadline_ms = value.get("deadline_ms")
    if type(deadline_ms) is not int or not 1 <= deadline_ms <= 3_600_000:
        raise HarnessIngressError("invalid deadline")

    rubric = value.get("rubric")
    source_identity = value.get("source_identity")
    if not isinstance(rubric, str) or not rubric or len(rubric) > 32_768:
        raise HarnessIngressError("invalid rubric")
    if not isinstance(source_identity, str) or not source_identity or len(source_identity) > 512:
        raise HarnessIngressError("invalid source identity")

    payload = {
        "schema_version": "1.0",
        "goal_id": _safe_id(value.get("goal_id"), "goal ID"),
        "receipt_id": _safe_id(value.get("receipt_id"), "receipt ID"),
        "attempt_id": _safe_id(value.get("attempt_id"), "attempt ID"),
        "source_identity": source_identity,
        "source_hashes": normalized_hashes,
        "rubric": rubric,
        "content": normalized_content,
        "deadline_ms": deadline_ms,
        "reserved_budget": {key: budget[key] for key in sorted(_BUDGET_KEYS)},
        "capability_attestation": {
            "tool_names": [], "source_mounted": False,
            "patch_channel": False, "arbitrary_command": False,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return NormalizedReviewRequest(harness, hashlib.sha256(canonical).hexdigest(), payload)


__all__ = [
    "HarnessIngressError", "NormalizedReviewRequest",
    "load_adapter_registry", "normalize_review_submission",
]
