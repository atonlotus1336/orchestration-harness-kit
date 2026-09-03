"""Provider-free, rollback-backed token-compute activation registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


HARNESS_IDS = (
    "hermes",
    "opencode",
    "pi",
    "antigravity",
    "codex",
    "claude-code",
    "moa",
    "cursor",
)
POLICY_KEYS = {"schema", "harnesses", "controls", "task_tiers", "promotion", "safety"}
CONTROL_VALUES = {
    "budget_admission": "active",
    "adaptive_depth": "evidence_gated_full_fallback",
    "context_cache": "active",
    "context_rendering": "active",
    "usage_accounting": "count_or_unavailable",
    "marginal_value_stop": "shadow",
}
REGISTRY_KEYS = {
    "schema",
    "policy_sha256",
    "mode",
    "targets",
    "controls",
    "approval_reference_sha256",
    "provider_calls",
    "provider_tokens",
    "pay_per_token",
    "service_restarts",
    "runtime_provider_activation",
}


class ActivationError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActivationError("activation JSON is unavailable") from exc
    if not isinstance(value, dict):
        raise ActivationError("activation JSON must be an object")
    return value


def load_policy(path: str | Path) -> tuple[dict[str, Any], str]:
    policy_path = Path(path)
    value = _read_json(policy_path)
    if set(value) != POLICY_KEYS or value.get("schema") != "lapart.token-compute-policy.v1":
        raise ActivationError("token-compute policy fields are not closed")
    if value.get("harnesses") != list(HARNESS_IDS):
        raise ActivationError("token-compute harness inventory is incomplete")
    if value.get("controls") != CONTROL_VALUES:
        raise ActivationError("safe token-compute control state changed")
    tiers = value.get("task_tiers")
    expected_tiers = ("atomic", "standard", "complex", "high_risk")
    if not isinstance(tiers, dict) or tuple(tiers) != expected_tiers:
        raise ActivationError("task-tier budget inventory is incomplete")
    previous = {key: 0 for key in next(iter(tiers.values()))}
    for name in expected_tiers:
        row = tiers[name]
        if not isinstance(row, dict) or set(row) != set(previous):
            raise ActivationError(f"{name} task-tier fields are not closed")
        if any(type(item) is not int or item <= previous[key] for key, item in row.items()):
            raise ActivationError(f"{name} task-tier limits are invalid or non-monotonic")
        previous = dict(row)
    promotion = value.get("promotion")
    if (
        not isinstance(promotion, dict)
        or promotion.get("minimum_samples_per_class_per_harness") != 100
        or promotion.get("minimum_wilson_lower_bound") != 0.75
        or set(promotion.get("required_gates", [])) != {"quality", "calls", "latency", "rollback", "tokens"}
    ):
        raise ActivationError("promotion gates are weaker than the safe baseline")
    safety = value.get("safety")
    expected_safety = {
        "subscription_first": True,
        "pay_per_token_allowed": False,
        "silent_provider_switch_allowed": False,
        "authority_deletion_allowed": False,
        "capability_discovery_removal_allowed": False,
        "provider_calls_during_activation": 0,
        "service_restarts_during_activation": 0,
    }
    if safety != expected_safety:
        raise ActivationError("token-compute safety boundary changed")
    return value, _sha256_bytes(policy_path.read_bytes())


def _target_state() -> dict[str, str]:
    return {
        "token_compute": "active",
        "budget_admission": "active",
        "adaptive_depth": "active_full_fallback_without_evidence",
        "context_cache": "active",
        "context_rendering": "active",
        "usage_accounting": "active",
        "marginal_value_stop": "shadow",
    }


def verify_registry(value: dict[str, Any], *, policy_sha256: str, require_all: bool = False) -> list[str]:
    failures: list[str] = []
    if set(value) != REGISTRY_KEYS or value.get("schema") != "lapart.token-compute-activation.v1":
        return ["activation registry fields are not closed"]
    if value.get("policy_sha256") != policy_sha256:
        failures.append("activation registry policy hash is stale")
    if value.get("mode") != "safe_active_shadow_stop" or value.get("controls") != CONTROL_VALUES:
        failures.append("activation registry control mode is unsafe")
    targets = value.get("targets")
    if not isinstance(targets, dict) or set(targets) - set(HARNESS_IDS):
        failures.append("activation registry target inventory is invalid")
    else:
        for name, state in targets.items():
            if name not in HARNESS_IDS or state != _target_state():
                failures.append(f"{name} activation state is invalid")
        if require_all and set(targets) != set(HARNESS_IDS):
            failures.append("not every harness is active")
    if not isinstance(value.get("approval_reference_sha256"), str) or len(value["approval_reference_sha256"]) != 64:
        failures.append("approval reference hash is invalid")
    expected_zero = {"provider_calls": 0, "provider_tokens": 0, "service_restarts": 0}
    if any(value.get(name) != expected for name, expected in expected_zero.items()):
        failures.append("activation performed a provider call, token use, or restart")
    if value.get("pay_per_token") is not False or value.get("runtime_provider_activation") != "unchanged":
        failures.append("activation crossed the provider boundary")
    return failures


def load_registry(path: str | Path, *, policy_sha256: str, require_all: bool = False) -> dict[str, Any]:
    value = _read_json(Path(path))
    failures = verify_registry(value, policy_sha256=policy_sha256, require_all=require_all)
    if failures:
        raise ActivationError("; ".join(failures))
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _snapshot(registry: Path) -> str | None:
    if not registry.is_file():
        return None
    content = registry.read_bytes()
    snapshot_id = _sha256_bytes(content)[:16]
    destination = registry.parent / "rollbacks" / "token-compute" / f"{snapshot_id}.json"
    if destination.is_file() and destination.read_bytes() != content:
        raise ActivationError("rollback snapshot identity collision")
    if not destination.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return snapshot_id


def activate(
    registry: str | Path,
    *,
    policy_path: str | Path,
    harnesses: Iterable[str],
    approval_reference: str,
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(approval_reference, str) or not approval_reference.strip():
        raise ActivationError("activation requires an approval reference")
    policy, policy_sha256 = load_policy(policy_path)
    requested = tuple(dict.fromkeys(harnesses))
    if not requested or any(name not in HARNESS_IDS for name in requested):
        raise ActivationError("activation contains an unsupported harness")
    registry_path = Path(registry)
    targets: dict[str, Any] = {}
    if registry_path.is_file():
        existing = _read_json(registry_path)
        existing_hash = existing.get("policy_sha256")
        failures = verify_registry(
            existing,
            policy_sha256=existing_hash if isinstance(existing_hash, str) else "",
        )
        if failures:
            raise ActivationError("existing activation registry is unsafe: " + "; ".join(failures))
        targets.update(existing["targets"])
    targets.update({name: _target_state() for name in requested})
    value = {
        "schema": "lapart.token-compute-activation.v1",
        "policy_sha256": policy_sha256,
        "mode": "safe_active_shadow_stop",
        "targets": {name: targets[name] for name in HARNESS_IDS if name in targets},
        "controls": dict(policy["controls"]),
        "approval_reference_sha256": _sha256_bytes(approval_reference.strip().encode("utf-8")),
        "provider_calls": 0,
        "provider_tokens": 0,
        "pay_per_token": False,
        "service_restarts": 0,
        "runtime_provider_activation": "unchanged",
    }
    failures = verify_registry(value, policy_sha256=policy_sha256)
    if failures:
        raise ActivationError("; ".join(failures))
    snapshot_id = _snapshot(registry_path)
    _atomic_json(registry_path, value)
    return value, snapshot_id


def rollback(registry: str | Path, snapshot_id: str) -> dict[str, Any]:
    if len(snapshot_id) != 16 or any(char not in "0123456789abcdef" for char in snapshot_id):
        raise ActivationError("rollback snapshot identifier is invalid")
    registry_path = Path(registry)
    snapshot = registry_path.parent / "rollbacks" / "token-compute" / f"{snapshot_id}.json"
    if not snapshot.is_file() or _sha256_bytes(snapshot.read_bytes())[:16] != snapshot_id:
        raise ActivationError("rollback snapshot is unavailable or invalid")
    value = _read_json(snapshot)
    _atomic_json(registry_path, value)
    return value
