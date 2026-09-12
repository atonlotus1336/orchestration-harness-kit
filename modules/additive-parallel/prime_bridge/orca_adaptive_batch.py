"""Parent-owned adaptive coding batches transported by Orca."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from .worker_transport import normalize_orca_worker_show


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _result(status: str, reason: str, *, provider_calls: int = 0,
            proposals=(), integration=(), aggregate=None, rollback=(),
            rollback_complete: bool | None = None, workers=()) -> dict[str, Any]:
    return {
        "status": status, "reason": reason, "provider": "orca",
        "provider_calls": provider_calls,
        "proposal_count": len(tuple(proposals)),
        "integration_sequence": list(integration),
        "aggregate_verification": aggregate,
        "rollback": list(rollback),
        "rollback_complete": rollback_complete,
        "workers": list(workers),
        "integration_authorized": status == "PASS",
        "verified_completion": status == "PASS",
    }


def _proposal(*, service, binding_id: str, child_id: str,
              workspace: Path, allowed_paths: Sequence[str],
              capability_receipt: str) -> dict[str, Any]:
    collection = service.provider.transport.collect(binding_id)
    receipt, _row = service.provider.transport._binding(binding_id)
    worktree = Path(receipt["worktree"]).resolve()
    root = workspace.resolve()
    changed = tuple(sorted(collection.get("files", {})))
    if not changed:
        raise ValueError("Orca worker returned no changed paths")
    if set(changed) - set(allowed_paths):
        raise ValueError("Orca worker changed an unadmitted path")
    manifest = []
    source, final = {}, {}
    for relative in changed:
        source_path = (root / relative).resolve()
        worker_path = (worktree / relative).resolve()
        if (not source_path.is_relative_to(root)
                or not worker_path.is_relative_to(worktree)):
            raise ValueError("Orca proposal escaped its workspace")
        before = source_path.read_bytes() if source_path.is_file() else b""
        if not worker_path.is_file() or worker_path.is_symlink():
            raise ValueError("Orca deletion or redirected output is unsupported")
        after = worker_path.read_bytes()
        operation = "replace" if source_path.is_file() else "create"
        before_hash = hashlib.sha256(before).hexdigest()
        after_hash = hashlib.sha256(after).hexdigest()
        source[relative] = before_hash
        final[relative] = after_hash
        manifest.append({
            "path": relative, "operation": operation,
            "before_hash": before_hash,
            "after_bytes_b64": base64.b64encode(after).decode("ascii"),
            "after_hash": after_hash,
        })
    omp_patch = {
        "manifest": manifest, "patch_hash": _digest(manifest),
        "source_hash": (
            next(iter(source.values())) if len(source) == 1 else _digest(source)
        ),
        "final_tree_hash": (
            next(iter(final.values())) if len(final) == 1 else _digest(final)
        ),
    }
    wrapper = {
        "omp_patch": omp_patch,
        "capability_receipt": capability_receipt,
    }
    return {
        "schema_version": "isolated-coding-proposal-v1",
        "child_session_id": child_id,
        "changed_paths": list(changed),
        "patch": wrapper, "patch_sha256": _digest(wrapper),
        "local_test_status": "PARENT_VERIFICATION_REQUIRED",
        "verified_completion": False,
        "completion_authority": "parent_only",
    }


async def execute_orca_adaptive_batch(
    *, service, broker, record: Mapping[str, Any], task: str,
    harness: str, workspace: Path,
    units: Sequence[Mapping[str, Any]], qualification_reference: str,
    apply_proposal, rollback_proposal, verify_aggregate,
    preauthorize: Callable[[str, str], Any], timeout_seconds: int,
) -> dict[str, Any]:
    """Start every admitted worker before waiting, then integrate serially."""
    if not units or len(units) < 2 or timeout_seconds < 1:
        return _result("FALLBACK", "ORCA_BATCH_INPUT_INVALID")
    provider_calls = 0
    workers: list[dict[str, Any]] = []
    started: list[tuple[Mapping[str, Any], str]] = []
    for unit in units:
        child_id = str(unit["child_session_id"])
        packet = dict(unit["task_packet"])
        start_arguments = {
            "objective": task,
            "task_spec": json.dumps(packet, sort_keys=True, separators=(",", ":")),
            "task_id": child_id,
            "contract_sha256": packet["packet_hash"],
            "declared_paths": list(unit["allowed_paths"]),
            "parallel_binding": True,
        }
        response = await broker.handle({
            "job_id": record["job_id"], "profile": record["profile"],
            "harness": harness, "alias": "orca.worker.start",
            "arguments": start_arguments,
        })
        if response.get("status") == "approval_required":
            request_id = str(response.get("request_id") or "")
            if not request_id:
                return _result("HOLD", "ORCA_APPROVAL_IDENTITY_MISSING", workers=workers)
            argument_hash = broker.policy.canonical_arguments(start_arguments).digest
            preauthorize(request_id, argument_hash)
            response = await broker.handle({
                "job_id": record["job_id"], "profile": record["profile"],
                "harness": harness, "alias": "orca.worker.start",
                "request_id": request_id, "arguments": start_arguments,
            })
        if response.get("status") != "ok":
            return _result(
                "FALLBACK", "ORCA_START_NOT_AUTHORIZED",
                provider_calls=provider_calls, workers=workers,
            )
        provider_calls += 1
        value = response.get("result") or {}
        binding_id = str(value.get("binding_id") or "")
        if not binding_id:
            return _result(
                "HOLD", "ORCA_START_IDENTITY_MISSING",
                provider_calls=provider_calls, workers=workers,
            )
        started.append((unit, binding_id))
        workers.append({
            "child_session_id": child_id, "binding_id": binding_id,
            "dispatch_id": value.get("dispatch_id"), "status": "started",
        })

    deadline = time.monotonic() + timeout_seconds
    pending = {binding_id for _unit, binding_id in started}
    while pending and time.monotonic() < deadline:
        for unit, binding_id in started:
            if binding_id not in pending:
                continue
            shown = await asyncio.to_thread(service.provider.show, binding_id)
            provider_calls += 1
            _dispatch, _worker, status = normalize_orca_worker_show(
                shown["worker_envelope"],
            )
            if status == "done":
                pending.remove(binding_id)
                for row in workers:
                    if row["binding_id"] == binding_id:
                        row["status"] = "done"
            elif status in {"failed", "cancelled"}:
                for _other_unit, other in started:
                    if other in pending and other != binding_id:
                        try:
                            await asyncio.to_thread(service.provider.stop, other)
                            provider_calls += 1
                        except Exception:
                            pass
                return _result(
                    "HOLD", "ORCA_WORKER_FAILED", provider_calls=provider_calls,
                    workers=workers,
                )
        if pending:
            await asyncio.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    if pending:
        for _unit, binding_id in started:
            if binding_id in pending:
                try:
                    await asyncio.to_thread(service.provider.stop, binding_id)
                    provider_calls += 1
                except Exception:
                    pass
        return _result(
            "HOLD", "ORCA_BATCH_TIMEOUT", provider_calls=provider_calls,
            workers=workers,
        )

    proposals = []
    try:
        for unit, binding_id in started:
            proposals.append(_proposal(
                service=service, binding_id=binding_id,
                child_id=str(unit["child_session_id"]), workspace=workspace,
                allowed_paths=unit["allowed_paths"],
                capability_receipt=qualification_reference,
            ))
    except Exception as exc:
        return _result(
            "HOLD", f"ORCA_PROPOSAL_COLLECTION_FAILED:{type(exc).__name__}",
            provider_calls=provider_calls, proposals=proposals, workers=workers,
        )

    applied = []
    sequence = []
    for index, (unit, proposal) in enumerate(zip(units, proposals), start=1):
        result = dict(apply_proposal(proposal, unit))
        if result.get("status") != "VERIFIED_OK" or "rollback_token" not in result:
            rollback = [dict(rollback_proposal(token)) for token in reversed(applied)]
            return _result(
                "HOLD", "SERIAL_PATCH_APPLICATION_FAILED",
                provider_calls=provider_calls, proposals=proposals,
                integration=sequence, rollback=rollback,
                rollback_complete=all(row.get("status") == "ROLLED_BACK" for row in rollback),
                workers=workers,
            )
        applied.append(result["rollback_token"])
        sequence.append({
            "position": index,
            "child_session_id": proposal["child_session_id"],
            "patch_sha256": proposal["patch_sha256"],
            "status": "VERIFIED_OK",
        })
    aggregate = dict(verify_aggregate(proposals))
    if aggregate.get("status") != "VERIFIED_OK":
        rollback = [dict(rollback_proposal(token)) for token in reversed(applied)]
        return _result(
            "HOLD", "AGGREGATE_VERIFICATION_FAILED",
            provider_calls=provider_calls, proposals=proposals,
            integration=sequence, aggregate=aggregate, rollback=rollback,
            rollback_complete=all(row.get("status") == "ROLLED_BACK" for row in rollback),
            workers=workers,
        )
    for _unit, binding_id in started:
        try:
            await asyncio.to_thread(service.provider.release, binding_id)
            provider_calls += 1
        except Exception:
            pass
    return _result(
        "PASS", "ORCA_ADAPTIVE_CODING_BATCH_VERIFIED",
        provider_calls=provider_calls, proposals=proposals,
        integration=sequence, aggregate=aggregate, workers=workers,
    )


__all__ = ["execute_orca_adaptive_batch"]
