"""Version-aware, fail-closed activation owner for adaptive parallelism."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from prime_bridge import adaptive_parallel_paths as state_paths

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DEFAULT_POLICY = ROOT / "config" / "adaptive-parallel-policy.json"
DEFAULT_LEGACY = ROOT / "config" / "additive-parallel-activation.json"
DEFAULT_QUALIFICATION = ROOT / ".planning" / "adaptive-parallel-qualification.json"
DEFAULT_RECEIPTS = ROOT / ".planning" / "adaptive-parallel-activation-receipts"
RUNTIME_SCHEDULER = WORKSPACE / "governed-executor-runtime" / "src" / "lapart_executor" / "adaptive_parallel.py"
REVIEW_SCHEDULER = ROOT / "prime_bridge" / "adaptive_review" / "review_scheduler.py"
CATALOG = ROOT / "config" / "adaptive-reviewer-catalog.json"
QUALIFICATION_STATES = {"candidate": 0, "canary_qualified": 1, "production_qualified": 2}
LAYER_ALIASES = {
    "execution": "execution_parallelism",
    "reviewer": "reviewer_concurrency",
}
ACTIVATION_LAYERS = (
    "execution_parallelism", "reviewer_concurrency", "career_kernel",
    "temperament", "career_shadow_pass",
)
LAYER_CAPABILITIES = {
    "execution_parallelism": "isolated_coding",
    "reviewer_concurrency": "review",
    "career_kernel": "career_kernel",
    "temperament": "temperament",
    "career_shadow_pass": "career_shadow_pass",
}
REVIEW_LAYERS = frozenset(ACTIVATION_LAYERS[1:])
ACCOUNTING_FIELDS = frozenset({
    "permission_bypasses", "lost_work", "duplicated_work",
    "unsafe_publications", "unaccounted_usage",
    "missed_seeded_critical_defects",
})
QUALITY_FIELDS = frozenset({
    "mandatory_recall", "precision", "schema_validity",
    "answerable_refusal_rate", "p95_latency_ratio",
})
TARGET_REQUIREMENTS = {
    "canary": ("candidate", "canary"),
    "pilot_limited": ("canary_qualified", "canary"),
    "pilot_wider": ("production_qualified", "limited"),
    "review": ("canary_qualified", "canary"),
    "production": ("production_qualified", "wider"),
}


class ActivationError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy_contract_digest(value: Mapping[str, Any]) -> str:
    value = json.loads(json.dumps(value))
    value.pop("activation_state", None)
    for layer in ("execution", "reviewer"):
        if isinstance(value.get(layer), dict):
            value[layer] = {
                key: item for key, item in value[layer].items()
                if key not in {"enabled", "qualification_state"}
            }
    if isinstance(value.get("grants"), dict):
        value["grants"] = {
            name: {"rollback_target": grant.get("rollback_target")}
            for name, grant in sorted(value["grants"].items())
        }
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _policy_contract_sha256(path: Path) -> str:
    return policy_contract_digest(json.loads(path.read_text(encoding="utf-8")))


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".adaptive-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ActivationError("expiry must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ActivationError("expiry must be UTC")
    return parsed


def source_binding(policy_path: Path = DEFAULT_POLICY) -> dict[str, str]:
    paths = {
        "policy_sha256": policy_path,
        "execution_scheduler_sha256": RUNTIME_SCHEDULER,
        "review_scheduler_sha256": REVIEW_SCHEDULER,
        "career_catalog_sha256": CATALOG,
    }
    if any(not path.is_file() for path in paths.values()):
        raise ActivationError("an installed adaptive source is missing")
    return {
        name: _policy_contract_sha256(path) if name == "policy_sha256" else _sha256(path)
        for name, path in paths.items()
    }


def load_legacy(path: Path = DEFAULT_LEGACY) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "1.0":
        raise ActivationError("unsupported legacy activation version")
    if (
        value.get("state") != "shadow_active"
        or value.get("activation_scope") != "independent_read_only_native_children"
        or value.get("max_concurrent_children") != 2
    ):
        raise ActivationError("legacy v1 activation meaning drifted")
    return value


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    from lapart_executor.adaptive_parallel import AdaptiveParallelPolicy
    AdaptiveParallelPolicy.from_mapping(value)
    return value


def _qualification(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "2.1", "records": {}, "current": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schema_version") != "2.1"
        or not isinstance(value.get("records"), dict)
        or not isinstance(value.get("current"), dict)
    ):
        raise ActivationError("qualification store is malformed")
    return value


def _canonical_layer(layer: str) -> str:
    value = LAYER_ALIASES.get(layer, layer)
    if value not in ACTIVATION_LAYERS:
        raise ActivationError("qualification layer is invalid")
    return value


def _evidence_hmac_sha256(value: Mapping[str, Any], authority_key: bytes) -> str:
    payload = {
        key: item for key, item in value.items()
        if key != "authority_hmac_sha256"
    }
    return hmac.new(
        authority_key,
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def _record_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _record_key(layer: str, evidence: Mapping[str, Any]) -> str:
    layer = _canonical_layer(layer)
    return "|".join((
        layer, evidence["harness"], evidence["profile"], evidence["model_route_sha256"],
        evidence["workload_class"], evidence["capability"], evidence.get("s_pipeline") or "-",
        evidence["policy_sha256"], str(evidence["layer_ceiling"]),
    ))


def _validate_evidence(
    evidence, *, layer, qualification_state, policy_path, now, authority_key,
):
    layer = _canonical_layer(layer)
    allowed = {
        "schema_version", "harness", "profile", "model_route_sha256", "workload_class",
        "capability", "s_pipeline", "expires_at", "budgets", "provider_free_passes",
        "live_trials", "live_failures", "soak_hours", "lineages",
        "multilineage_capacity", "rollback_exercised", "expiry_exercised", "scope",
        "policy_sha256", "layer_ceiling", "matched_pairs", "quality", "accounting",
        "qualification_predecessor_sha256", "limited_predecessor_sha256",
        "authority_hmac_sha256",
    }
    if set(evidence) != allowed or evidence.get("schema_version") != "2.0":
        raise ActivationError("qualification evidence fields are incomplete or unknown")
    if qualification_state not in QUALIFICATION_STATES:
        raise ActivationError("qualification layer or state is invalid")
    for field in ("harness", "profile", "model_route_sha256", "workload_class", "capability"):
        if not isinstance(evidence[field], str) or not evidence[field]:
            raise ActivationError(f"qualification {field} is invalid")
    policy = load_policy(policy_path)
    expected_capability = LAYER_CAPABILITIES[layer]
    if evidence["capability"] != expected_capability:
        raise ActivationError(
            f"{layer} qualification capability must be {expected_capability!r}"
        )
    if len(evidence["model_route_sha256"]) != 64:
        raise ActivationError("model route fingerprint is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", evidence["model_route_sha256"]) is None:
        raise ActivationError("model route fingerprint is invalid")
    if evidence["policy_sha256"] != source_binding(policy_path)["policy_sha256"]:
        raise ActivationError("qualification policy fingerprint is stale")
    expected_ceiling = (
        policy["execution"]["qualified_ceiling"]
        if layer == "execution_parallelism"
        else policy["reviewer"]["qualified_ceiling"]
        if layer == "reviewer_concurrency"
        else 1
    )
    if type(evidence["layer_ceiling"]) is not int or evidence["layer_ceiling"] != expected_ceiling:
        raise ActivationError("qualification layer ceiling is invalid")
    if layer in REVIEW_LAYERS and evidence["s_pipeline"] not in {"S0", "S1", "S2", "S3", "S4"}:
        raise ActivationError("reviewer qualification requires an S pipeline")
    if layer == "execution_parallelism" and evidence["s_pipeline"] is not None:
        raise ActivationError("execution qualification cannot claim an S pipeline")
    if (
        not isinstance(evidence["budgets"], Mapping)
        or set(evidence["budgets"]) != {
            "max_calls", "max_tokens", "max_wall_seconds", "max_cost",
        }
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for value in evidence["budgets"].values()
        )
    ):
        raise ActivationError("qualification budgets are invalid")
    for field in ("provider_free_passes", "live_trials", "live_failures", "lineages", "multilineage_capacity", "matched_pairs"):
        if type(evidence[field]) is not int or evidence[field] < 0:
            raise ActivationError(f"qualification {field} is invalid")
    if isinstance(evidence["soak_hours"], bool) or not isinstance(evidence["soak_hours"], (int, float)):
        raise ActivationError("qualification soak hours are invalid")
    if _utc(evidence["expires_at"]) <= now:
        raise ActivationError("qualification evidence is expired")
    if set(evidence["quality"]) != QUALITY_FIELDS or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in evidence["quality"].values()
    ):
        raise ActivationError("qualification quality metrics are invalid")
    quality = evidence["quality"]
    if not (
        0 <= quality["mandatory_recall"] <= 1
        and 0 <= quality["precision"] <= 1
        and 0 <= quality["schema_validity"] <= 1
        and 0 <= quality["answerable_refusal_rate"] <= 1
        and quality["p95_latency_ratio"] >= 0
    ):
        raise ActivationError("qualification quality metrics are invalid")
    if set(evidence["accounting"]) != ACCOUNTING_FIELDS or any(
        type(value) is not int or value < 0
        for value in evidence["accounting"].values()
    ):
        raise ActivationError("qualification accounting metrics are invalid")
    if any(evidence["accounting"].values()):
        raise ActivationError("qualification has an accounting or safety failure")
    predecessor = evidence["limited_predecessor_sha256"]
    transition_predecessor = evidence["qualification_predecessor_sha256"]
    for label, value in (
        ("limited predecessor", predecessor),
        ("qualification predecessor", transition_predecessor),
    ):
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ActivationError(f"{label} fingerprint is invalid")
    if not authority_key or not hmac.compare_digest(
        evidence["authority_hmac_sha256"],
        _evidence_hmac_sha256(evidence, authority_key),
    ):
        raise ActivationError("qualification authority signature is invalid")
    if evidence["provider_free_passes"] < 1 or evidence["live_failures"]:
        raise ActivationError("candidate qualification evidence is insufficient")
    if QUALIFICATION_STATES[qualification_state] >= 1 and (
        evidence["live_trials"] < 5
        or evidence["matched_pairs"] < 5
        or quality["mandatory_recall"] < 1
        or quality["precision"] < 1
        or quality["schema_validity"] < .95
        or quality["answerable_refusal_rate"] > .10
        or quality["p95_latency_ratio"] > 2
    ):
        raise ActivationError("canary qualification quality gate is incomplete")
    if qualification_state == "production_qualified":
        thresholds = {"limited": (24, 20), "wider": (72, 50)}
        if evidence["scope"] not in thresholds:
            raise ActivationError("production qualification scope is invalid")
        hours, lineages = thresholds[evidence["scope"]]
        if evidence["soak_hours"] < hours or evidence["lineages"] < lineages:
            raise ActivationError("production soak requires both time and lineage volume")
        if (
            evidence["multilineage_capacity"] < 2
            or evidence["rollback_exercised"] is not True
            or evidence["expiry_exercised"] is not True
        ):
            raise ActivationError("production qualification controls are incomplete")
        if evidence["scope"] == "limited" and predecessor is not None:
            raise ActivationError("limited production evidence cannot claim a predecessor")
        if evidence["scope"] == "wider" and predecessor is None:
            raise ActivationError("wider production evidence requires a limited predecessor")
    record = dict(evidence)
    record.update(
        layer=layer, qualification_state=qualification_state,
        source_binding=source_binding(policy_path),
        qualified_at=now.isoformat().replace("+00:00", "Z"),
    )
    return record


class ActivationManager:
    def __init__(self, *, policy_path=None, legacy_path=DEFAULT_LEGACY,
                 qualification_path=None, receipts_path=None,
                 authority_key=None):
        self.policy_path = state_paths.policy_path(policy_path)
        self.legacy_path = Path(legacy_path)
        self.qualification_path = state_paths.qualification_path(qualification_path)
        self.receipts_path = state_paths.receipts_path(receipts_path)
        raw_key = authority_key
        if raw_key is None:
            raw_key = os.environ.get("LAPART_QUALIFICATION_HMAC_KEY")
        if isinstance(raw_key, str):
            raw_key = raw_key.encode()
        self.authority_key = raw_key if isinstance(raw_key, bytes) and len(raw_key) >= 16 else None

    def plan(self, *, target, layers):
        policy = load_policy(self.policy_path)
        load_legacy(self.legacy_path)
        canonical_layers = tuple(_canonical_layer(layer) for layer in layers)
        if target not in TARGET_REQUIREMENTS or not canonical_layers:
            raise ActivationError("promotion plan target or layers are invalid")
        requested = set(canonical_layers)
        enabled = {
            name for name, grant in policy["grants"].items() if grant["enabled"]
        } | requested
        if (
            {"temperament", "career_shadow_pass"} & enabled
            and "career_kernel" not in enabled
        ):
            raise ActivationError("career modifiers require the career kernel")
        if "career_kernel" in enabled and "reviewer_concurrency" not in enabled:
            raise ActivationError("career kernel requires reviewer concurrency")
        return {
            "status": "PLANNED", "writes_performed": False,
            "current_state": policy["activation_state"], "target_state": target,
            "layers": list(canonical_layers), "legacy_v1_preserved": True,
            "source_binding": source_binding(self.policy_path),
        }

    def qualify(self, *, layer, qualification_state, evidence, now=None):
        layer = _canonical_layer(layer)
        if self.authority_key is None:
            raise ActivationError("qualification authority key is unavailable")
        current = now or datetime.now(timezone.utc)
        record = _validate_evidence(
            evidence, layer=layer, qualification_state=qualification_state,
            policy_path=self.policy_path, now=current,
            authority_key=self.authority_key,
        )
        store = _qualification(self.qualification_path)
        identity_key = _record_key(layer, evidence)
        current_digest = store["current"].get(identity_key)
        existing = store["records"].get(current_digest) if current_digest else None
        expected_prior = {
            "candidate": None,
            "canary_qualified": "candidate",
            "production_qualified": (
                "production_qualified" if evidence["scope"] == "wider"
                else "canary_qualified"
            ),
        }[qualification_state]
        if expected_prior is None:
            if existing is not None:
                raise ActivationError("candidate qualification already has a current record")
            if evidence["qualification_predecessor_sha256"] is not None:
                raise ActivationError("candidate qualification cannot claim a predecessor")
        else:
            if (
                existing is None
                or existing["qualification_state"] != expected_prior
                or evidence["qualification_predecessor_sha256"] != current_digest
            ):
                raise ActivationError("qualification state transition is invalid")
        if qualification_state == "production_qualified" and evidence["scope"] == "wider":
            if (
                existing.get("scope") != "limited"
                or evidence["limited_predecessor_sha256"] != current_digest
            ):
                raise ActivationError("wider production predecessor is not the current limited record")
        record_digest = _record_sha256(record)
        if record_digest in store["records"] and store["records"][record_digest] != record:
            raise ActivationError("qualification record digest collision")
        store["records"][record_digest] = record
        store["current"][identity_key] = record_digest
        _atomic(self.qualification_path, store)
        return {
            "status": "QUALIFIED", "record_key": record_digest,
            "identity_key": identity_key, "record": record,
        }

    def promote(self, *, target, layers, harness, profile, model_route_sha256,
                workload_class, s_pipeline=None, now=None):
        current = now or datetime.now(timezone.utc)
        self.plan(target=target, layers=layers)
        canonical_layers = tuple(_canonical_layer(layer) for layer in layers)
        store = _qualification(self.qualification_path)
        binding = source_binding(self.policy_path)
        selected = {}
        policy = load_policy(self.policy_path)
        for layer in canonical_layers:
            evidence = {
                "harness": harness, "profile": profile, "model_route_sha256": model_route_sha256,
                "workload_class": workload_class,
                "capability": LAYER_CAPABILITIES[layer],
                "s_pipeline": s_pipeline if layer in REVIEW_LAYERS else None,
                "policy_sha256": binding["policy_sha256"],
                "layer_ceiling": (
                    policy["execution"]["qualified_ceiling"]
                    if layer == "execution_parallelism"
                    else policy["reviewer"]["qualified_ceiling"]
                    if layer == "reviewer_concurrency"
                    else 1
                ),
            }
            record_digest = store["current"].get(_record_key(layer, evidence))
            record = store["records"].get(record_digest) if record_digest else None
            required, required_scope = TARGET_REQUIREMENTS[target]
            if (
                record is None
                or QUALIFICATION_STATES[record["qualification_state"]] < QUALIFICATION_STATES[required]
                or record["scope"] != required_scope
                or record["source_binding"] != binding
                or _utc(record["expires_at"]) <= current
                or _record_sha256(record) != record_digest
            ):
                raise ActivationError(f"{layer} has no current {required} grant")
            selected[layer] = (record_digest, record)
        policy = load_policy(self.policy_path)
        required, required_scope = TARGET_REQUIREMENTS[target]
        for layer, grant in policy["grants"].items():
            if grant["enabled"] and layer not in selected:
                sibling = store["records"].get(grant["qualification_reference"])
                if (
                    sibling is None
                    or _record_sha256(sibling) != grant["qualification_reference"]
                    or sibling["layer"] != layer
                    or sibling["qualification_state"] != grant["state"]
                    or sibling["scope"] != required_scope
                    or QUALIFICATION_STATES[grant["state"]] < QUALIFICATION_STATES[required]
                    or grant["expiry"] is None
                    or _utc(grant["expiry"]) <= current
                ):
                    raise ActivationError(
                        f"{layer} must be requalified before global {target} promotion"
                    )
        policy["activation_state"] = target
        for layer, (record_digest, record) in selected.items():
            grant = policy["grants"][layer]
            grant.update(
                enabled=True,
                state=record["qualification_state"],
                qualification_reference=record_digest,
                expiry=record["expires_at"],
            )
        policy["execution"]["enabled"] = policy["grants"]["execution_parallelism"]["enabled"]
        policy["reviewer"]["enabled"] = policy["grants"]["reviewer_concurrency"]["enabled"]
        if "execution_parallelism" in selected:
            policy["execution"]["qualification_state"] = selected["execution_parallelism"][1]["qualification_state"]
        if "reviewer_concurrency" in selected:
            policy["reviewer"]["qualification_state"] = selected["reviewer_concurrency"][1]["qualification_state"]
        _atomic(self.policy_path, policy)
        receipt = {
            "schema_version": "2.0", "action": "promote", "state": target,
            "layers": list(canonical_layers), "grants": {
                layer: hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                for layer, (_record_digest, record) in selected.items()
            },
            "legacy_v1_preserved": True,
            "created_at": current.isoformat().replace("+00:00", "Z"),
        }
        receipt["receipt_sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        _atomic(self.receipts_path / f'{receipt["receipt_sha256"]}.json', receipt)
        return receipt

    def rollback(self, *, reason, outstanding_liabilities=None, now=None):
        if not reason:
            raise ActivationError("rollback reason is required")
        current = now or datetime.now(timezone.utc)
        policy = load_policy(self.policy_path)
        prior = policy["activation_state"]
        policy["activation_state"] = "inactive"
        policy["execution"]["enabled"] = False
        policy["reviewer"]["enabled"] = False
        for grant in policy["grants"].values():
            grant["enabled"] = False
        _atomic(self.policy_path, policy)
        receipt = {
            "schema_version": "2.0", "action": "rollback", "prior_state": prior,
            "state": "inactive", "reason": reason, "admit_new_parallel_batches": False,
            "terminate_existing_children_automatically": False, "preserve_evidence": True,
            "reconcile_liabilities_once": dict(outstanding_liabilities or {}),
            "created_at": current.isoformat().replace("+00:00", "Z"),
        }
        receipt["receipt_sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        _atomic(self.receipts_path / f'{receipt["receipt_sha256"]}.json', receipt)
        return receipt

    def status(self):
        policy = load_policy(self.policy_path)
        legacy = load_legacy(self.legacy_path)
        state = policy["activation_state"]
        execution_active = state in {
            "canary", "pilot_limited", "pilot_wider", "production",
        }
        reviewer_active = state in {
            "canary", "pilot_limited", "pilot_wider", "review", "production",
        }
        layers = {}
        for name in ACTIVATION_LAYERS:
            policy_key = (
                "execution" if name == "execution_parallelism"
                else "reviewer" if name == "reviewer_concurrency"
                else None
            )
            layer = policy[policy_key] if policy_key else None
            grant = policy["grants"][name]
            layer_active = (
                execution_active if name == "execution_parallelism"
                else reviewer_active
            )
            actual = (
                min(layer["qualified_ceiling"], layer["desired_ceiling"])
                if layer_active and layer is not None and layer["enabled"]
                else 0
            )
            layers[name] = {
                "desired_count": layer["desired_ceiling"] if layer else int(grant["enabled"]),
                "actual_ceiling": actual if layer else int(reviewer_active and grant["enabled"]),
                "qualification_state": grant["state"], "enabled": grant["enabled"],
                "qualification_reference": grant["qualification_reference"],
                "expiry": grant["expiry"],
            }
        unavailable = []
        if state == "inactive":
            unavailable.append("INACTIVE")
        elif state == "shadow":
            unavailable.append("SHADOW_OBSERVATION_ONLY")
        if reviewer_active and policy["grants"]["career_kernel"]["enabled"]:
            unavailable.append("RUNTIME_CAREER_ASSIGNMENTS_NOT_SAMPLED")
        unavailable.extend((
            "RUNTIME_REVIEW_QUEUE_NOT_SAMPLED",
            "RUNTIME_RESERVATIONS_NOT_SAMPLED",
            "RUNTIME_CONVERGENCE_NOT_SAMPLED",
        ))
        return {
            "schema_version": "2.0", "state": state, "strategy": policy["strategy"],
            "layers": layers, "active_career_count": None, "active_career_ids": None,
            "review_queue": None, "reservations": None, "convergence_status": None,
            "limiting_reasons": unavailable,
            "next_action": (
                "qualify" if state in {"inactive", "shadow"}
                else "observe_runtime_and_soak"
            ),
            "legacy_v1": {
                "state": legacy["state"], "max_concurrent_children": legacy["max_concurrent_children"],
                "meaning_preserved": True,
            },
        }
