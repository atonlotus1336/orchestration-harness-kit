"""Shared Prime-parent execution contract for coding-plan harnesses."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prime_bridge.usage_ledger import STAGE_TIMING_KEYS, UsageLedger, UsageLedgerError, make_usage_event
from prime_bridge.budget_policy import BudgetPolicy, BudgetPolicyError, classify_task
from prime_bridge.autonomy_intent import detect_autonomous_local_intent
from prime_bridge.adaptive_depth import AdaptiveDepthPolicy
from prime_bridge.checkpoints import write_checkpoint, write_step_checkpoint
from prime_bridge.context_cache import ContextCache, sha256_json, workspace_fingerprint
from prime_bridge.context_efficiency import RenderingPolicy, project_context
from prime_bridge.marginal_value import MarginalValuePolicy, safe_decide
from prime_bridge.verified_success import is_verified_success
from prime_bridge.execution_target import windows_path_to_wsl

from prime_bridge.auto_handoff import ponytail_snapshot
from prime_bridge.task_authorization import make_task_authorization
from prime_bridge.omp_task_packet import (
    RegressionTestEvidence,
    OmpTaskPacket,
    OmpTaskPacketError,
    SCHEMA_VERSION as TASK_PACKET_SCHEMA_VERSION,
    build_change_contract,
    compile_atomic_task_packet,
    project_packet_depth,
)

RECEIPT_SCHEMA_VERSION = "3.0"
PROTOCOL_VERSION = "omp-proposal-success-phase0"
VERIFIED_HOST_RESULTS = frozenset({"verified", "VERIFIED_OK"})
GOVERNED_PRIME_LANES = frozenset({"T3_PRIME_OMP", "T4_AUTONOMOUS"})


def _resolved_route_fingerprint(service: Any, execution_id: str) -> str:
    """Resolve a provider/model route without substituting service-class identity."""
    resolver = getattr(service, "resolved_route", None)
    route = resolver() if callable(resolver) else None
    fingerprint = route.get("fingerprint") if isinstance(route, dict) else getattr(service, "route_fingerprint", None)
    if isinstance(fingerprint, str) and len(fingerprint) == 64 and all(ch in "0123456789abcdef" for ch in fingerprint):
        return fingerprint
    return hashlib.sha256(f"UNAVAILABLE_MODEL_ROUTE:{execution_id}".encode()).hexdigest()


def _bounded_diagnostic(value: Any) -> dict[str, Any] | None:
    """Preserve diagnostic evidence without retaining prompt or source content."""
    if value in (None, ""):
        return None
    rendered = str(value)
    return {
        "sha256": hashlib.sha256(rendered.encode("utf-8", errors="replace")).hexdigest(),
        "length": len(rendered),
    }


def _safe_exact_error(value: Any) -> str | None:
    """Keep actionable typed failures while never copying likely secret material."""
    if value in (None, ""):
        return None
    rendered = str(value)
    lowered = rendered.lower()
    if any(marker in lowered for marker in ("secret", "token", "password", "private key", "api_key", "canary")):
        return None
    return rendered


def _locally_known_contract_blocker(service: Any, execution_lane: str) -> dict[str, str] | None:
    """Return a token-free blocker only for locally provable settlement defects.

    Provider/transport health is intentionally excluded: a later RPC failure is
    evidence-bearing runtime uncertainty, not a deterministic pre-dispatch fact.
    """
    if execution_lane not in GOVERNED_PRIME_LANES:
        return None
    bridge = getattr(service, "governed_session_bridge", None)
    unavailable_reason = getattr(bridge, "unavailable_reason", None)
    if bridge is not None and isinstance(unavailable_reason, str) and unavailable_reason:
        return {
            "reason": "GOVERNED_SESSION_CONFIGURATION_UNAVAILABLE",
            "owner": "governed_session_bridge",
            "detail": unavailable_reason,
        }
    return None

def _child_proposal_count(child: dict[str, Any]) -> int:
    """Return the child record's proposal count only when it is a non-negative int."""
    value = (child.get("record") or {}).get("sdk_evidence", {}).get("proposal_count")
    if type(value) is int and value >= 0:
        return value
    return 0


def _task_class(task: str) -> str:
    normalized = task.lower()
    if "test" in normalized:
        return "coding_with_tests"
    if "doc" in normalized:
        return "documentation"
    return "coding"


def normalize_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Normalize v2 evidence into the v3 receipt surface without inventing data."""
    value = dict(receipt)
    if value.get("schema_version") == RECEIPT_SCHEMA_VERSION:
        return value
    if value.get("schema_version") != "2.0":
        raise ValueError("unsupported coding receipt schema")
    value.update({
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "compatibility_source_schema": "2.0",
        "protocol_version": "legacy-v2",
        "component_versions": {},
        "stage_timings_ms": {},
        "task_class": "unknown",
        "declared_paths": [],
        "model_route_fingerprint": None,
        "proposal_count": 0,
        "review_decision": None,
        "host_result": None,
    })
    return value


class CodingExecutionError(RuntimeError):
    """The governed Prime -> OMP execution contract was not satisfied."""


def canonicalize_declared_paths(
    workspace: Path,
    declared_paths: list[str] | tuple[str, ...],
    *,
    require_regular_files: bool = False,
) -> list[str]:
    """Normalize user-facing paths to the OMP packet's project-relative form.

    Prime/OMP packets intentionally accept only canonical POSIX paths.  The
    CLI, however, is commonly called from Windows-facing surfaces that may
    supply drive-letter, UNC, absolute WSL, or backslash-separated paths.  Do
    the translation once at the host boundary and keep the packet/receipt/
    fallback scope identical thereafter.

    Existing directories are accepted only when ``require_regular_files`` is
    false so callers can produce a safe native-continuation receipt.  The OMP
    worktree itself remains file-only; broad directory scopes must never be
    silently expanded into an unbounded child allowlist.
    """
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise CodingExecutionError("workspace is not a directory")
    if not isinstance(declared_paths, (list, tuple)) or not declared_paths:
        raise CodingExecutionError("at least one declared path is required")

    normalized: list[str] = []
    for raw in declared_paths:
        if not isinstance(raw, str) or not raw.strip():
            raise CodingExecutionError("declared paths must be non-empty strings")
        value = raw.strip()
        try:
            if ":\\" in value or ":/" in value or value.startswith("\\\\") or value.startswith("//"):
                value = windows_path_to_wsl(value)
            else:
                value = value.replace("\\", "/")
        except ValueError as exc:
            raise CodingExecutionError(f"declared path cannot be translated: {raw}") from exc

        candidate_input = Path(value)
        unresolved = candidate_input if candidate_input.is_absolute() else root / candidate_input
        if unresolved.is_symlink() or any(parent.is_symlink() for parent in unresolved.parents if parent != root and root in parent.parents):
            raise CodingExecutionError("declared path cannot contain symlinks")
        candidate = unresolved.resolve(strict=False)
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise CodingExecutionError("declared path escapes the workspace") from exc
        canonical = relative.as_posix()
        if canonical in {"", "."} or any(part in {"", ".", ".."} for part in Path(canonical).parts):
            raise CodingExecutionError("declared path must identify a project-relative entry")
        if canonical in normalized:
            raise CodingExecutionError("declared paths contain duplicates")
        if require_regular_files and candidate.exists() and not candidate.is_file():
            raise CodingExecutionError("OMP declared path is not a regular file")
        normalized.append(canonical)
    return normalized


def task_packet_public(packet: OmpTaskPacket) -> dict[str, Any]:
    """Return the complete closed packet plus its canonical binding hash."""
    return {**packet.canonical_data(), "packet_hash": packet.packet_hash}


def compile_declared_task_packets(
    task: str,
    declared_paths: list[str] | tuple[str, ...],
    semantic_assertions: list[str] | tuple[str, ...] = (),
    verification_command_id: str | None = None,
    risk_tier_override: str | None = None,
    acceptance_criteria: list[str] | tuple[str, ...] = (),
    bugfix_intent: bool | None = None,
    regression_test_evidence: RegressionTestEvidence | None = None,
    regression_test_exemption: str | None = None,
    alternative_verification: list[str] | tuple[str, ...] = (),
    cleanup_boundary_enabled: bool = True,
) -> list[OmpTaskPacket]:
    """Compile stable bounded packets; broad path sets become dependency-ordered units."""
    if not isinstance(task, str) or not task.strip():
        raise CodingExecutionError("coding task is required")
    if not isinstance(declared_paths, (list, tuple)) or not declared_paths:
        raise CodingExecutionError("at least one declared path is required")
    paths = list(declared_paths)
    if len(paths) != len(set(paths)):
        raise CodingExecutionError("declared paths contain duplicates")
    verification_intent = (
        () if verification_command_id is None
        else (f"command:{verification_command_id}",)
    )
    if risk_tier_override not in {None, "low", "moderate", "high"}:
        raise CodingExecutionError("packet risk tier override is invalid")
    if len(paths) == 1:
        try:
            return [compile_atomic_task_packet(
                objective=task, path=paths[0],
                verification_intent=verification_intent,
                semantic_assertions=tuple(semantic_assertions),
                risk_tier=risk_tier_override or "low",
                acceptance_criteria=tuple(acceptance_criteria),
                bugfix_intent=bugfix_intent,
                regression_test_evidence=regression_test_evidence,
                regression_test_exemption=regression_test_exemption,
                alternative_verification=tuple(alternative_verification),
                cleanup_boundary_enabled=cleanup_boundary_enabled,
            )]
        except OmpTaskPacketError as exc:
            raise CodingExecutionError(str(exc)) from exc
    packets: list[OmpTaskPacket] = []
    dependency: tuple[str, ...] = ()
    for offset in range(0, len(paths), 8):
        chunk = paths[offset:offset + 8]
        chunk_assertions = []
        for assertion in semantic_assertions:
            parts = assertion.split(":", 2) if isinstance(assertion, str) else []
            if len(parts) != 3:
                raise CodingExecutionError("semantic assertion format is invalid")
            if parts[1] in chunk:
                chunk_assertions.append(assertion)
        contract = build_change_contract(
            objective=task.strip(), declared_paths=tuple(chunk),
            semantic_assertions=tuple(chunk_assertions),
            acceptance_criteria=tuple(acceptance_criteria),
            bugfix_intent=bugfix_intent,
            regression_test_evidence=regression_test_evidence,
            regression_test_exemption=regression_test_exemption,
            alternative_verification=tuple(alternative_verification),
            cleanup_boundary_enabled=cleanup_boundary_enabled,
        )
        value = {
            "schema_version": TASK_PACKET_SCHEMA_VERSION,
            "objective": task.strip(),
            "edit_class": "multi_file",
            "allowed_paths": chunk,
            "required_observations": [f"read:{path}" for path in chunk],
            "invariants": ["remain within declared paths", "preserve unrelated behavior"],
            "risk_tier": risk_tier_override or "moderate",
            "size_budget": {
                "max_files": len(chunk),
                "max_changed_bytes": 65536 * len(chunk),
                "max_complexity": max(4, len(chunk) * 2),
            },
            "verification_intent": list(verification_intent),
            "semantic_assertions": chunk_assertions,
            "change_contract": contract.canonical_data(),
            "dependencies": list(dependency),
        }
        try:
            packet = OmpTaskPacket.from_mapping(value)
        except OmpTaskPacketError as exc:
            raise CodingExecutionError(str(exc)) from exc
        packets.append(packet)
        dependency = (packet.packet_hash,)
    return packets


def build_coding_prompt(task: str, *, harness: str) -> str:
    if not isinstance(task, str) or not task.strip():
        raise CodingExecutionError("coding task is required")
    ponytail = ponytail_snapshot()
    if not ponytail.get("available"):
        raise CodingExecutionError("canonical Ponytail skill is unavailable")
    return (
        f"{ponytail['content']}\n\n"
        "## Mandatory governed coding route\n"
        f"Calling harness: {harness}\n"
        "You are the parent implementation controller. The Hermes runtime will attach "
        "exactly one automatic OMP child result before your implementation pass. "
        "Inspect the OMP patch and SDK evidence, reject unsafe or out-of-scope changes, and "
        "return a concise integration brief for the host harness. The host harness remains "
        "the authoritative writer and verifier. Do not bypass OMP, recursively invoke GSD, "
        "or claim completion without an OMP child receipt.\n\n"
        f"## Coding task\n{task}"
    )


def _receipt_path(receipt_root: Path, execution_id: str) -> Path:
    return receipt_root / f"{execution_id}.json"


def write_receipt(receipt_root: Path, value: dict[str, Any]) -> Path:
    receipt_root.mkdir(parents=True, exist_ok=True)
    path = _receipt_path(receipt_root, value["execution_id"])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


async def dispatch_coding_execution(
    service: Any, *, task: str, workspace: Path, project: str, harness: str,
    receipt_root: Path, declared_paths: list[str] | tuple[str, ...] | None = None,
    packet_override: OmpTaskPacket | None = None,
    semantic_assertions: list[str] | tuple[str, ...] = (),
    verification_command_id: str | None = None,
    acceptance_criteria: list[str] | tuple[str, ...] = (),
    bugfix_intent: bool | None = None,
    regression_test_evidence: RegressionTestEvidence | None = None,
    regression_test_exemption: str | None = None,
    alternative_verification: list[str] | tuple[str, ...] = (),
    cleanup_boundary_enabled: bool = True,
    allow_unavailable_fallback: bool = True,
    usage_ledger: UsageLedger | None = None,
    retry_count: int = 0,
    budget_policy: BudgetPolicy | None = None,
    budget_demand: dict[str, int | float] | None = None,
    requested_tier: str | None = None,
    depth_policy: AdaptiveDepthPolicy | None = None,
    depth_evidence: dict[str, Any] | None = None,
    context_cache: ContextCache | None = None,
    rendering_policy: RenderingPolicy | None = None,
    context_sections: list[dict[str, Any]] | None = None,
    bounded_summaries: list[dict[str, Any]] | None = None,
    summary_sources: dict[str, str] | None = None,
    stop_policy: MarginalValuePolicy | None = None,
    stop_state: dict[str, Any] | None = None,
    stop_promotion: dict[str, Any] | None = None,
    max_total_tokens: int | None = None,
    unattended: bool = False,
    verified_native_recovery: bool = False,
    fail_closed: bool = False,
    execution_lane: str = "T3_PRIME_OMP",
    execution_value: dict[str, Any] | None = None,
    rlm_promotion_trial: bool = False,
    durability_required: bool = False,
) -> dict[str, Any]:
    """Thin receipt adapter over the canonical service pipeline."""
    execution_id = uuid.uuid4().hex
    monotonic_started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    autonomy = detect_autonomous_local_intent(task)
    if unattended and not autonomy["activated"]:
        autonomy = {
            **autonomy,
            "activated": True,
            "profile": "autonomous-local",
            "signal": "explicit_unattended_override",
            "reason": "EXPLICIT_UNATTENDED_OVERRIDE",
        }
    autonomous_local = unattended or autonomy["activated"]
    native_recovery = verified_native_recovery or (autonomy["activated"] and not fail_closed)
    legacy_declaration_adapter = declared_paths is None and packet_override is None
    if declared_paths is not None:
        declared_paths = canonicalize_declared_paths(
            workspace, declared_paths, require_regular_files=True,
        )
    execution_score = (execution_value or {}).get("score", {})
    packet_risk_tier = (
        execution_score.get("risk_tier")
        if execution_lane == "T2_OMP_LITE"
        and (execution_value or {}).get("actual_lane") == "T2_OMP_LITE"
        else None
    )
    packets = [packet_override] if packet_override is not None else (
        compile_declared_task_packets(
            task, declared_paths, semantic_assertions, verification_command_id,
            risk_tier_override=packet_risk_tier,
            acceptance_criteria=acceptance_criteria,
            bugfix_intent=bugfix_intent,
            regression_test_evidence=regression_test_evidence,
            regression_test_exemption=regression_test_exemption,
            alternative_verification=alternative_verification,
            cleanup_boundary_enabled=cleanup_boundary_enabled,
        )
        if declared_paths is not None else []
    )
    task_tier = "UNAVAILABLE"
    route_fingerprint = _resolved_route_fingerprint(service, execution_id)
    depth_decision = None
    cache_result = {"status": "DISABLED", "key_fingerprint": None, "bytes_reused": 0, "reason": "CACHE_UNAVAILABLE"}
    if context_cache is not None:
        workspace_revision = workspace_fingerprint(workspace, list(declared_paths or ()))
        policy_revision = sha256_json({
            "budget": budget_policy.tiers if budget_policy else None,
            "depth": depth_policy.profiles if depth_policy else None,
            "evidence": sha256_json(depth_evidence) if isinstance(depth_evidence, dict) else None,
            "requested_tier": requested_tier,
            "execution_lane": execution_lane,
            "execution_policy": (execution_value or {}).get("policy_fingerprint"),
        })
        cache_fields = {
            "artifact_type": "policy_projection",
            "workspace_revision": workspace_revision,
            "policy_revision": policy_revision,
            "route_fingerprint": route_fingerprint,
            "input_hash": hashlib.sha256(task.encode()).hexdigest(),
            "schema_version": "1.0",
        }
        cache_result = context_cache.get(cache_fields)
        if cache_result["status"] == "HIT":
            projection = cache_result.get("value")
            if (not isinstance(projection, dict) or set(projection) != {"task_tier", "depth_profile"}
                    or projection["task_tier"] not in {"atomic", "standard", "complex", "high_risk", "UNAVAILABLE"}
                    or projection["depth_profile"] not in {"compact", "balanced", "full"}):
                cache_result = {"status": "MISS", "value": None, "key_fingerprint": cache_result["key_fingerprint"],
                                "bytes_reused": 0, "reason": "CORRUPT"}
            else:
                task_tier = projection["task_tier"]
                if depth_policy:
                    depth_decision = {"profile": projection["depth_profile"], "reason": "CACHE_HIT",
                                      "evidence_fingerprint": None, "limits": depth_policy.limits_for(projection["depth_profile"])}
    if task_tier == "UNAVAILABLE":
        task_tier = classify_task(task, list(declared_paths or ()), requested_tier) if budget_policy else "UNAVAILABLE"
    if depth_decision is None:
        depth_decision = depth_policy.select(
            task_tier=task_tier if task_tier != "UNAVAILABLE" else "high_risk",
            harness=harness, route_fingerprint=route_fingerprint, evidence=depth_evidence,
            current_execution_id=execution_id,
        ) if depth_policy else None
    if context_cache is not None and cache_result["status"] == "MISS":
        projection = {"task_tier": task_tier, "depth_profile": depth_decision["profile"] if depth_decision else "full"}
        stored = context_cache.put(cache_fields, projection)
        cache_result = {**cache_result, "stored": stored["status"] == "STORED"}
    if depth_decision is not None:
        packets = [project_packet_depth(packet, depth_decision["limits"]) for packet in packets]
    packet_records = [task_packet_public(packet) for packet in packets]
    context_result = {"projection": None, "packet_bytes": 0, "retrieved_bytes": 0,
                      "summary_bytes": 0, "dropped_section_categories": [],
                      "context_pressure_band": "UNAVAILABLE", "rendering_profile": "UNAVAILABLE", "model_calls": 0}
    boundary_states = stop_state or {}
    if "rule" in boundary_states:
        boundary_states = {str(boundary_states["rule"]): boundary_states}
    stop_decisions = []
    for rule in ("retry", "repair", "context_expansion"):
        values = boundary_states.get(rule, {}) if isinstance(boundary_states, dict) else {}
        at_boundary = (rule == "repair" or (rule == "retry" and retry_count > 0)
                       or (rule == "context_expansion" and bool(context_sections or bounded_summaries)))
        if not at_boundary:
            stop_decisions.append({"decision": "CONTINUE", "reason": "MARGINAL_VALUE_AVAILABLE", "rule": rule})
            continue
        decision = safe_decide(
            stop_policy,
            task_tier=task_tier if task_tier != "UNAVAILABLE" else "high_risk",
            rule=rule,
            remaining_budget=values.get("remaining_budget", 1),
            expected_success_gain=values.get("expected_success_gain", 1.0),
            promotion=stop_promotion,
        ) if stop_policy else {"decision": "CONTINUE", "reason": "POLICY_UNAVAILABLE", "rule": rule}
        stop_decisions.append(decision)
    stop_decision = next((item for item in stop_decisions if item["decision"] != "CONTINUE"), stop_decisions[0])
    context_stop = next((item for item in stop_decisions
                         if item["rule"] == "context_expansion" and item["decision"] == "ENFORCED_STOP"), None)
    if rendering_policy and context_stop is None:
        context_result = project_context(
            policy=rendering_policy, task_tier=task_tier if task_tier != "UNAVAILABLE" else "high_risk",
            profile="governed", surface="task_packet", sections=context_sections or (),
            bounded_summaries=bounded_summaries or (), summary_sources=summary_sources or {},
        )
    base = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "component_versions": {"coding_execution": RECEIPT_SCHEMA_VERSION},
        "execution_id": execution_id,
        "started_at": started_at,
        "harness": harness,
        "workspace": str(workspace.resolve()),
        "project": project,
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
        "task_class": _task_class(task),
        "execution_lane": execution_lane,
        "execution_value": dict(execution_value or {}),
        "task_tier": task_tier,
        "declared_paths": list(declared_paths or ()),
        "semantic_assertion_count": len(semantic_assertions),
        "task_packet_hash": packets[0].packet_hash if len(packets) == 1 else None,
        "model_route_fingerprint": route_fingerprint,
        "requested_profile": getattr(service, "profile", None),
        "autonomy": autonomy,
        "autonomy_profile": "autonomous-local" if autonomous_local else "conservative",
        "autonomous_local": autonomous_local,
        "task_authorization": make_task_authorization(
            task=task, project=project, declared_paths=list(declared_paths or ()),
            profile=str(getattr(service, "profile", "native")), autonomy=autonomy,
        ),
        "stage_timings_ms": {
            "routing": max(0, int((time.monotonic() - monotonic_started) * 1000)),
        },
        "depth_profile": depth_decision["profile"] if depth_decision else "full",
        "depth_reason": depth_decision["reason"] if depth_decision else "POLICY_UNAVAILABLE",
        "depth_evidence_fingerprint": depth_decision["evidence_fingerprint"] if depth_decision else None,
        "cache_status": cache_result["status"],
        "cache_key_fingerprint": cache_result["key_fingerprint"],
        "cache_bytes_reused": cache_result["bytes_reused"],
        "cache_reason": cache_result["reason"],
        "context_metrics": {key: context_result[key] for key in ("packet_bytes", "retrieved_bytes", "summary_bytes", "dropped_section_categories", "context_pressure_band", "rendering_profile", "model_calls")},
        "stop_decision": stop_decision,
        "stop_decisions": stop_decisions,
    }
    durable_step_enabled = execution_lane == "T4_AUTONOMOUS" or (
        execution_lane == "T3_PRIME_OMP" and durability_required
    )
    durable_step_id = "governed-dispatch"
    durable_step_input = packets[0].packet_hash if len(packets) == 1 else base["task_sha256"]
    durable_step_key = hashlib.sha256(
        f"{base['task_sha256']}:{durable_step_input}:{execution_lane}:{durable_step_id}".encode("utf-8")
    ).hexdigest()

    def persist_durable_step(
        *, status: str, pending_write_status: str,
        output_reference: str | None, evidence: dict[str, Any], next_safe_action: str,
    ) -> Path:
        persistence_started = time.monotonic()
        path = write_step_checkpoint(
            receipt_root,
            execution_id=execution_id,
            execution_lane=execution_lane,
            step_id=durable_step_id,
            input_fingerprint=durable_step_input,
            idempotency_key=durable_step_key,
            status=status,
            pending_write_status=pending_write_status,
            output_reference=output_reference,
            evidence=evidence,
            next_safe_action=next_safe_action,
        )
        elapsed = max(0, int((time.monotonic() - persistence_started) * 1000))
        base["stage_timings_ms"]["persistence"] = base["stage_timings_ms"].get("persistence", 0) + elapsed
        base["durable_step"] = {
            "enabled": True,
            "checkpoint_path": str(path),
            "step_id": durable_step_id,
            "idempotency_key": durable_step_key,
            "status": status,
            "pending_write_status": pending_write_status,
            "model_calls": 0,
        }
        return path

    def finish(receipt: dict[str, Any], provider_usage: Any = None) -> dict[str, Any]:
        elapsed_ms = max(0, int((time.monotonic() - monotonic_started) * 1000))
        receipt["wall_time_ms"] = elapsed_ms
        if isinstance(provider_usage, dict):
            safe_usage = {
                key: value for key, value in provider_usage.items()
                if key in {"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"}
                and type(value) is int and value >= 0
            }
            if safe_usage:
                receipt["usage"] = safe_usage
        if receipt.get("outcome") == "blocked":
            persistence_started = time.monotonic()
            checkpoint = write_checkpoint(
                receipt_root,
                execution_id=execution_id,
                task_sha256=base["task_sha256"],
                declared_paths=base["declared_paths"],
                completed_units=receipt.get("completed_units", ()),
                pending_units=receipt.get("pending_units", ()),
                blocker=str(receipt.get("reason", "GOVERNANCE_BLOCKED")),
                evidence={
                    "stop_decision": receipt.get("stop_decision", base["stop_decision"]),
                    "budget_admission": receipt.get("budget_admission"),
                },
                next_safe_action=str(receipt.get(
                    "next_safe_action", "Resume from this checkpoint after resolving the exact blocker.",
                )),
            )
            persistence_elapsed = max(0, int((time.monotonic() - persistence_started) * 1000))
            base["stage_timings_ms"]["persistence"] = (
                base["stage_timings_ms"].get("persistence", 0) + persistence_elapsed
            )
            receipt = {**receipt, "checkpoint_path": str(checkpoint)}
        receipt["stage_timings_ms"] = dict(sorted(base["stage_timings_ms"].items()))
        if "durable_step" in base:
            receipt["durable_step"] = dict(base["durable_step"])
        if usage_ledger is not None:
            try:
                event = make_usage_event(
                    execution_id=execution_id,
                    harness=harness,
                    task_class=base["task_class"],
                    model_route_fingerprint=str(receipt.get("model_route_fingerprint") or base["model_route_fingerprint"]),
                    outcome=str(receipt.get("outcome", "failed")),
                    usage=provider_usage if isinstance(provider_usage, dict) else None,
                    wall_time_ms=elapsed_ms,
                    retry_count=retry_count,
                    verified=is_verified_success(receipt),
                    task_tier=base["task_tier"],
                    depth_profile=base["depth_profile"],
                    depth_evidence_fingerprint=base["depth_evidence_fingerprint"],
                    cache_status=base["cache_status"] if base["cache_status"] in {"HIT", "MISS", "DISABLED"} else "UNAVAILABLE",
                    cache_artifact_type="policy_projection" if context_cache is not None else "UNAVAILABLE",
                    cache_key_fingerprint=base["cache_key_fingerprint"],
                    cache_bytes_reused=base["cache_bytes_reused"],
                    cache_reason=base["cache_reason"],
                    packet_bytes=base["context_metrics"]["packet_bytes"],
                    retrieved_bytes=base["context_metrics"]["retrieved_bytes"],
                    summary_bytes=base["context_metrics"]["summary_bytes"],
                    dropped_section_categories=base["context_metrics"]["dropped_section_categories"],
                    context_pressure_band=base["context_metrics"]["context_pressure_band"],
                    rendering_profile=base["context_metrics"]["rendering_profile"],
                    stop_decision=base["stop_decision"]["decision"],
                    stop_reason=base["stop_decision"]["reason"],
                    stop_rule=base["stop_decision"]["rule"],
                    stage_timings_ms=receipt.get("stage_timings_ms", base["stage_timings_ms"]),
                )
                usage_ledger.append(event)
                receipt["usage_event_id"] = event["event_id"]
                receipt["usage_event_status"] = "RECORDED"
            except UsageLedgerError:
                receipt["usage_event_status"] = "REJECTED"
        receipt["receipt_path"] = str(write_receipt(receipt_root, receipt))
        return receipt

    contract_blocker = _locally_known_contract_blocker(service, execution_lane)
    if contract_blocker is not None:
        return finish({
            **base,
            "outcome": "blocked",
            "reason": contract_blocker["reason"],
            "pre_dispatch_contract": {
                "status": "BLOCKED",
                "owner": contract_blocker["owner"],
                "detail": contract_blocker["detail"],
                "provider_dependent": False,
            },
            "prime_active": False,
            "omp_active": False,
            "native_fallback_authorized": False,
            "integration_authorized": False,
            "proposal_count": 0,
            "review_decision": None,
            "host_result": None,
            "model_requests": 0,
            "provider_calls": 0,
            "provider_tokens": 0,
        })

    blocking_stop = next((item for item in stop_decisions
                          if item["decision"] == "ENFORCED_STOP" and item["rule"] in {"retry", "context_expansion"}), None)
    if blocking_stop is not None:
        degraded_stop = {
            **blocking_stop,
            "decision": "DEGRADED_CONTINUE",
            "original_decision": "ENFORCED_STOP",
            "reason": "CONTINUED_AT_REDUCED_DEPTH",
        }
        stop_decisions = [degraded_stop if item is blocking_stop else item for item in stop_decisions]
        base["stop_decisions"] = stop_decisions
        base["stop_decision"] = degraded_stop

    if not legacy_declaration_adapter and len(packets) != 1:
        if not autonomous_local:
            return finish({
                **base,
                "outcome": "blocked",
                "reason": "TASK_REQUIRES_DECOMPOSITION",
                "prime_active": False,
                "omp_active": False,
                "native_fallback_authorized": False,
                "integration_authorized": False,
                "decomposition_units": packet_records,
                "pending_units": packet_records,
                "next_safe_action": "Activate autonomous-local for bounded in-scope decomposition or split the task explicitly.",
                "proposal_count": 0,
                "review_decision": None,
                "host_result": None,
            })
        rlm_executor = getattr(service, "execute_rlm_coding_plan", None)
        if callable(rlm_executor) and 2 <= len(packets) <= 4:
            rlm_result = await rlm_executor({
                "task": task, "harness": harness, "workspace": str(workspace),
                "project": project, "session_id": f"rlm-{execution_id}",
                "task_packets": packet_records, "unattended": True,
                "promotion_trial": rlm_promotion_trial,
            })
            if rlm_result.get("outcome") != "direct":
                return finish({
                    **base, **rlm_result,
                    "prime_active": True, "omp_active": True,
                    "native_fallback_authorized": False,
                    "decomposition_units": packet_records,
                    "completed_units": packet_records if rlm_result.get("outcome") == "complete" else [],
                    "pending_units": [] if rlm_result.get("outcome") == "complete" else packet_records,
                })
        completed_units: list[dict[str, Any]] = []
        pending_units = list(packet_records)
        unit_results: list[dict[str, Any]] = []
        for index, packet in enumerate(packets):
            result = await dispatch_coding_execution(
                service,
                task=task,
                workspace=workspace,
                project=project,
                harness=harness,
                receipt_root=receipt_root,
                declared_paths=list(packet.allowed_paths),
                packet_override=packet,
                semantic_assertions=list(packet.semantic_assertions),
                allow_unavailable_fallback=allow_unavailable_fallback,
                usage_ledger=usage_ledger,
                retry_count=retry_count,
                budget_policy=budget_policy,
                budget_demand=budget_demand,
                requested_tier=requested_tier,
                depth_policy=depth_policy,
                depth_evidence=depth_evidence,
                context_cache=context_cache,
                rendering_policy=rendering_policy,
                context_sections=context_sections,
                bounded_summaries=bounded_summaries,
                summary_sources=summary_sources,
                stop_policy=stop_policy,
                stop_state=stop_state,
                stop_promotion=stop_promotion,
                unattended=True,
                verified_native_recovery=native_recovery,
                fail_closed=fail_closed,
                durability_required=durability_required or len(packets) > 1,
            )
            unit_results.append({
                "unit": index + 1,
                "packet_hash": packet.packet_hash,
                "allowed_paths": list(packet.allowed_paths),
                "outcome": result.get("outcome"),
                "reason": result.get("reason"),
            })
            if result.get("outcome") in {"complete", "fallback"}:
                completed_units.append(unit_results[-1])
                pending_units = pending_units[1:]
                continue
            break
        if len(completed_units) == len(packets):
            outcome = "complete" if all(item["outcome"] == "complete" for item in unit_results) else "fallback"
            reason = "AUTONOMOUS_DECOMPOSITION_COMPLETE" if outcome == "complete" else "AUTONOMOUS_DECOMPOSITION_CONTINUATION"
        else:
            outcome = "blocked"
            reason = unit_results[-1].get("reason", "DECOMPOSITION_UNIT_BLOCKED") if unit_results else "DECOMPOSITION_UNIT_BLOCKED"
        return finish({
            **base,
            "outcome": outcome,
            "reason": reason,
            "prime_active": any(item.get("outcome") == "complete" for item in unit_results),
            "omp_active": any(item.get("outcome") == "complete" for item in unit_results),
            "native_fallback_authorized": outcome in {"complete", "fallback"},
            "integration_authorized": outcome == "complete",
            "decomposition_units": unit_results,
            "completed_units": completed_units,
            "pending_units": pending_units,
            "proposal_count": 0,
            "review_decision": None,
            "host_result": None,
        })

    if budget_policy is not None:
        demand = {
            "context_bytes": len(task.encode("utf-8")),
            "output_tokens": 0,
            "wall_seconds": 0,
            "tool_calls": 0,
            "omp_repair_turns": 0,
            "prime_review_bytes": 0,
            "provider_calls": 1,
        }
        if budget_demand is not None:
            demand.update(budget_demand)
        try:
            admission = budget_policy.admit(task_tier, demand)
        except BudgetPolicyError as exc:
            admission = {"decision": "DENIED", "reason": "BUDGET_POLICY_INVALID", "detail": type(exc).__name__}
        if admission["decision"] == "DENIED":
            hard_budget_boundary = (
                fail_closed
                or task_tier == "high_risk"
                or budget_demand is not None and budget_demand.get("hard_cap") is True
            )
            if not hard_budget_boundary and admission["reason"] != "BUDGET_POLICY_INVALID":
                return finish({
                    **base,
                    "outcome": "fallback" if allow_unavailable_fallback else "blocked",
                    "reason": "BUDGET_DEGRADED_NATIVE_CONTINUATION",
                    "budget_admission": {**admission, "decision": "DEGRADED_NATIVE_CONTINUE"},
                    "prime_active": False,
                    "omp_active": False,
                    "native_fallback_authorized": allow_unavailable_fallback,
                    "integration_authorized": False,
                    "proposal_count": 0,
                    "review_decision": None,
                    "host_result": None,
                })
            if admission["reason"] == "BUDGET_POLICY_INVALID":
                budget_policy = None
            else:
                return finish({
                    **base,
                    "outcome": "blocked",
                    "reason": admission["reason"],
                    "budget_admission": admission,
                    "prime_active": False,
                    "omp_active": False,
                    "native_fallback_authorized": False,
                    "integration_authorized": False,
                    "proposal_count": 0,
                    "review_decision": None,
                    "host_result": None,
                })
    if durable_step_enabled:
        try:
            persist_durable_step(
                status="pending",
                pending_write_status="not_started",
                output_reference=None,
                evidence={"provider_calls": 0, "task_packet_hash": base["task_packet_hash"]},
                next_safe_action="Resume this exact governed dispatch using its idempotency key.",
            )
        except (OSError, ValueError) as step_error:
            return finish({
                **base,
                "outcome": "blocked",
                "reason": "DURABLE_STEP_CHECKPOINT_UNAVAILABLE",
                "durable_step_error": _bounded_diagnostic(step_error),
                "prime_active": False,
                "omp_active": False,
                "native_fallback_authorized": False,
                "integration_authorized": False,
                "proposal_count": 0,
                "review_decision": None,
                "host_result": None,
                "provider_calls": 0,
                "model_requests": 0,
            })

    try:
        execution_budget = budget_policy.limits_for(task_tier) if budget_policy else None
        if execution_budget is not None and any(
            item["decision"] == "ENFORCED_STOP" and item["rule"] == "repair" for item in stop_decisions
        ):
            execution_budget["max_omp_repair_turns"] = 0
        executor = service.execute_omp_lite if execution_lane == "T2_OMP_LITE" else service.execute_coding_plan
        # One provider-independent observer for every governed harness. A failed
        # observer never promotes shadow review or changes execution authority.
        evidence_adapter = None
        evidence_path = None
        if len(packets) == 1 and packets[0].change_contract is not None and execution_value:
            try:
                from prime_bridge.evidence_adapter import EvidenceAdapter
                evidence_adapter = EvidenceAdapter(workspace)
                evidence_path = evidence_adapter.begin(
                    packet=packets[0], harness=harness, run_id=execution_id,
                    execution_value=execution_value, authorization=base["task_authorization"],
                    lifecycle={"kind": "job_ledger", "project": project,
                               "session_id": f"{harness}-{execution_id}"},
                )
            except (OSError, ValueError, RuntimeError) as evidence_error:
                base["change_evidence"] = {
                    "status": "INCONCLUSIVE", "error_type": type(evidence_error).__name__,
                    "review_passed": False, "advisory": True, "model_calls": 0,
                }
        handoff_started = time.monotonic()
        pipeline = await executor({
            "task": task,
            **({"prompt": task} if execution_lane == "T2_OMP_LITE" else {}),
            **({"task_packet": packet_records[0]} if packet_records else {}),
            "harness": harness,
            "workspace": str(workspace.resolve()),
            "project": project,
            "session_id": f"{harness}-{execution_id}",
            "wait": True,
            "unattended": autonomous_local,
            "verified_native_recovery": native_recovery,
            "fail_closed": fail_closed,
            **({"execution_budget": execution_budget, "task_tier": task_tier} if execution_budget is not None else {}),
            **({"depth_profile": depth_decision["profile"], "depth_limits": depth_decision["limits"]} if depth_decision else {}),
            **({"context_projection": context_result["projection"]} if context_result["projection"] is not None else {}),
            "stop_decisions": stop_decisions,
            **({"max_total_tokens": max_total_tokens} if max_total_tokens is not None else {}),
            **({
                "omp_lite_maximum_files": (execution_value or {}).get("score", {}).get(
                    "omp_lite_maximum_files", 2,
                ),
            } if execution_lane == "T2_OMP_LITE" else {}),
        })
        base["stage_timings_ms"]["handoff"] = max(
            0, int((time.monotonic() - handoff_started) * 1000),
        )
        reported_stages = pipeline.get("stage_timings_ms")
        if isinstance(reported_stages, dict):
            for key, value in reported_stages.items():
                if key in STAGE_TIMING_KEYS and key != "handoff" and type(value) is int and value >= 0:
                    base["stage_timings_ms"][key] = value
    except Exception as exc:  # availability/admission is an evidence-bearing fallback
        if "handoff_started" in locals():
            base["stage_timings_ms"]["handoff"] = max(
                0, int((time.monotonic() - handoff_started) * 1000),
            )
        if durable_step_enabled:
            try:
                persist_durable_step(
                    status="blocked",
                    pending_write_status="unknown",
                    output_reference=None,
                    evidence={"error_type": type(exc).__name__},
                    next_safe_action="Inspect the bounded failure and resume the same idempotent step.",
                )
            except (OSError, ValueError) as step_error:
                base["durable_step"] = {
                    "enabled": True,
                    "status": "INCONCLUSIVE",
                    "error": _bounded_diagnostic(step_error),
                    "model_calls": 0,
                }
        receipt = {
            **base,
            "outcome": "fallback" if allow_unavailable_fallback and not fail_closed else "blocked",
            "reason": f"PRIME_UNAVAILABLE:{type(exc).__name__}",
            "prime_active": False,
            "omp_active": False,
            "native_fallback_authorized": allow_unavailable_fallback and not fail_closed,
            "proposal_count": 0,
            "review_decision": None,
            "host_result": None,
            "underlying_error": _safe_exact_error(exc),
            "bounded_diagnostic": _bounded_diagnostic(exc),
        }
        return finish(receipt)

    parent = pipeline.get("parent") or {}
    parent_id = str(parent.get("job_id", ""))
    omp_children = pipeline.get("omp_children") or []
    compliant = pipeline.get("outcome") == "complete" and pipeline.get("integration_authorized") is True
    recovery_receipt = pipeline.get("recovery_receipt")
    recovered = pipeline.get("outcome") == "fallback" and isinstance(recovery_receipt, dict) \
        and recovery_receipt.get("outcome") == "fallback"
    receipt = {
        **base,
        "outcome": "complete" if compliant else ("fallback" if recovered else "blocked"),
        "reason": pipeline.get("reason", "PRIME_OMP_CONTRACT_UNSATISFIED"),
        "execution_lane": execution_lane,
        "prime_active": pipeline.get("prime_active", execution_lane != "T2_OMP_LITE"),
        "prime_job_id": parent_id,
        "prime_status": parent.get("status"),
        "prime_result_summary": parent.get("result_summary"),
        "omp_active": bool(omp_children),
        "omp_child_job_ids": [str(child["child_job_id"]) for child in omp_children],
        "omp_statuses": [str(child["status"]) for child in omp_children],
        "omp_errors": [
            _bounded_diagnostic((child.get("record") or {}).get("error"))
            for child in omp_children
        ],
        "omp_underlying_errors": [
            exact
            for child in omp_children
            for exact in [_safe_exact_error((child.get("record") or {}).get("error"))]
            if exact is not None
        ],
        "prime_review": pipeline.get("prime_review"),
        "host_apply": pipeline.get("host_apply"),
        "proposal_count": sum(_child_proposal_count(child) for child in omp_children),
        "review_decision": (pipeline.get("prime_review") or {}).get("decision"),
        "host_result": (pipeline.get("host_apply") or {}).get("status"),
        "provider_calls": pipeline.get("provider_calls"),
        "model_requests": pipeline.get("model_requests"),
        "tool_calls": pipeline.get("tool_calls"),
        "provider_call_accounting_complete": pipeline.get("provider_call_accounting_complete"),
        "provider_call_components": pipeline.get("provider_call_components"),
        "usage_complete": pipeline.get("usage_complete"),
        "usage_components": pipeline.get("usage_components"),
        "native_fallback_authorized": recovered,
        "integration_authorized": compliant,
        "task_packet_hash": pipeline.get(
            "task_packet_hash", packets[0].packet_hash if packets else None,
        ),
        "requested_profile": parent.get("profile"),
        "resolved_provider": (parent.get("route") or {}).get("provider"),
        "resolved_model": (parent.get("route") or {}).get("model"),
        "model_route_fingerprint": (parent.get("route") or {}).get("fingerprint", base["model_route_fingerprint"]),
    }
    if evidence_adapter is not None and evidence_path is not None:
        evidence_started = time.monotonic()
        try:
            evidence = evidence_adapter.finish(
                evidence_path, job_ledger=getattr(service, "ledger", None),
                job_id=parent_id if parent_id and getattr(service, "ledger", None) is not None else None,
            )
            receipt["change_evidence"] = {
                "receipt_path": str(evidence_path), "evidence_sha256": evidence["evidence_sha256"],
                "status": evidence["execution_evidence_status"],
                "review_pipeline": evidence["review_pipeline"], "review_verdict": evidence["review_verdict"],
                **evidence["consumption"], "model_calls": 0,
            }
        except (OSError, ValueError, RuntimeError) as evidence_error:
            receipt["change_evidence"] = {
                "status": "INCONCLUSIVE", "receipt_path": str(evidence_path),
                "error_type": type(evidence_error).__name__, "review_passed": False,
                "advisory": True, "model_calls": 0,
            }
        finally:
            base["stage_timings_ms"]["evidence_collection"] = max(
                0, int((time.monotonic() - evidence_started) * 1000),
            )
    if recovered:
        for key in (
            "declared_paths", "changed_paths", "validation", "before_after_hashes",
            "rollback_status", "root_error", "recovery_error", "resolved_provider",
            "resolved_model", "route_fingerprint",
        ):
            if key in recovery_receipt:
                receipt[key] = recovery_receipt[key]
        receipt["recovery_receipt_path"] = recovery_receipt.get("receipt_path")
    if durable_step_enabled:
        step_completed = receipt.get("outcome") == "complete" and receipt.get("integration_authorized") is True
        try:
            persist_durable_step(
                status="completed" if step_completed else "blocked",
                pending_write_status="committed" if step_completed else (
                    "discarded" if receipt.get("outcome") == "fallback" else "unknown"
                ),
                output_reference=(f"job:{parent_id}" if parent_id else f"execution:{execution_id}"),
                evidence={
                    "outcome": receipt.get("outcome"),
                    "integration_authorized": receipt.get("integration_authorized") is True,
                    "provider_calls": receipt.get("provider_calls"),
                },
                next_safe_action=(
                    "No resume required; the governed step completed."
                    if step_completed
                    else "Resolve the recorded blocker and resume the same idempotent governed step."
                ),
            )
        except (OSError, ValueError) as step_error:
            receipt.update({
                "outcome": "blocked",
                "reason": "DURABLE_STEP_FINALIZATION_FAILED",
                "integration_authorized": False,
                "native_fallback_authorized": False,
                "durable_step_error": _bounded_diagnostic(step_error),
            })
    receipt["stage_timings_ms"] = dict(sorted(base["stage_timings_ms"].items()))
    provider_usage = pipeline.get("usage") or parent.get("usage")
    return finish(receipt, provider_usage)


def run_dispatch(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(dispatch_coding_execution(**kwargs))
