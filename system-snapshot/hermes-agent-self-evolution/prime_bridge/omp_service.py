"""Hermes-owned capability handler for bounded Prime-to-OMP child jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from prime_bridge.constrained_test_runner import TestCommandPolicy
from prime_bridge.job_ledger import JobLedger
from prime_bridge.omp_policy import OmpCodingPolicy, OmpPolicyError, admit_omp
from prime_bridge.omp_sdk_runner import OmpSdkSidecarRunner
from prime_bridge.omp_worker import OmpWorker
from prime_bridge.omp_worktree import OmpWorktree


class OmpServiceError(RuntimeError):
    pass


def bounded_repair_turns(budget: dict[str, Any]) -> int:
    """Reserve calls for the selected lane, then cap OMP protocol repairs."""
    prime_required = budget.get("prime_required", True) is not False
    reserve = 2 if prime_required else 1
    provider_calls = int(budget.get("provider_calls", reserve))
    if provider_calls < reserve:
        if prime_required:
            raise OmpServiceError("Prime and OMP require at least two provider calls")
        raise OmpServiceError("OMP-lite requires at least one provider call")
    return min(int(budget.get("repair_turns", 1)), max(0, provider_calls - reserve))


def assess_patch_integrity(patch: Any) -> dict[str, Any]:
    """Reject malformed manifests and obviously destructive line-count changes."""
    if not isinstance(patch, dict):
        return {"status": "rejected", "reason": "PATCH_MISSING"}
    manifest = patch.get("manifest")
    changed_paths = patch.get("changed_paths")
    patch_text = patch.get("patch")
    if not isinstance(manifest, list) or not manifest or not isinstance(changed_paths, list) or not isinstance(patch_text, str):
        return {"status": "rejected", "reason": "PATCH_MANIFEST_INVALID"}
    manifest_paths = [item.get("path") for item in manifest if isinstance(item, dict)]
    if manifest_paths != changed_paths or len(manifest_paths) != len(manifest) or any(not isinstance(path, str) or not path for path in manifest_paths):
        return {"status": "rejected", "reason": "PATCH_MANIFEST_MISMATCH"}
    if hashlib.sha256(patch_text.encode("utf-8")).hexdigest() != patch.get("patch_hash"):
        return {"status": "rejected", "reason": "PATCH_HASH_MISMATCH"}
    for before, after in re.findall(r"^@@ -1,(\d+) \+1,(\d+) @@$", patch_text, re.MULTILINE):
        if int(before) >= 100 and int(after) <= max(5, int(before) // 10):
            return {"status": "rejected", "reason": "PATCH_SUSPICIOUS_TRUNCATION"}
    return {"status": "accepted", "reason": "PATCH_INTEGRITY_OK"}


class OmpChildService:
    """Create and record OMP children only after the Prime broker authorizes it."""

    def __init__(self, *, root: Path, ledger: JobLedger, capability_policy: Any):
        self.root = Path(root).resolve()
        self.ledger = ledger
        self.capability_policy = capability_policy
        self.omp_policy = OmpCodingPolicy.load(self.root / "config" / "omp-coding-policy.json")
        self.test_policy = TestCommandPolicy.load(self.root / "config" / "omp-test-command-policy.json")
        lock = json.loads((self.root / "config" / "omp-runtime-lock.json").read_text(encoding="utf-8"))
        self.image = str(lock["image"].get("tag") or lock["image"]["digest"])
        self.feasibility_path = self.root / ".planning" / "phases" / "10-prime-governed-omp-coding" / "evidence" / "omp-feasibility.json"
        self._results: dict[str, dict[str, Any]] = {}

    def result_for(self, child_job_id: str) -> dict[str, Any] | None:
        return self._results.pop(child_job_id, None)

    def _feasibility(self) -> dict[str, Any]:
        try:
            value = json.loads(self.feasibility_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OmpServiceError("OMP feasibility evidence is unavailable") from exc
        if value.get("verdict") != "READY":
            raise OmpServiceError("OMP feasibility is not READY")
        return value

    async def call(self, tool: str, arguments: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
        if tool != "run":
            raise OmpServiceError("OMP internal tool is denied")
        if set(arguments) != {"prompt"} or not isinstance(arguments.get("prompt"), str) or not arguments["prompt"].strip():
            raise OmpServiceError("OMP run requires only a non-empty prompt")
        parent = self.ledger.get(context["job_id"])
        workspace = Path(parent["workspace"]).resolve()
        if workspace not in self.ledger.allowed_roots and not any(root in workspace.parents for root in self.ledger.allowed_roots):
            raise OmpServiceError("OMP workspace is outside the approved roots")
        route = parent["route"]
        feasibility = self._feasibility()
        model_route = {
            "status": "READY", "explicit_fallback": None,
            "route": {"fingerprint": route["fingerprint"]},
        }
        budget_events = [
            event for event in self.ledger.events(parent["job_id"])
            if event.get("kind") == "governed_execution_budget"
        ]
        budget_authority = budget_events[-1].get("data", {}) if budget_events else {}
        if (
            budget_authority.get("status") != "READY"
            or budget_authority.get("source") not in {"prime-job-admission", "governed-router"}
            or not all(
                isinstance(budget_authority.get(key), int) and budget_authority[key] >= minimum
                for key, minimum in (("wall_seconds", 1), ("tool_calls", 1), ("provider_calls", 1), ("repair_turns", 0))
            )
        ):
            raise OmpServiceError("durable shared budget authority is unavailable")
        try:
            admission = admit_omp(
                feasibility=feasibility, prime_job=parent, project_root=workspace,
                profile=context["profile"], model_route=model_route,
                capability_policy={"enabled": self.capability_policy.enabled},
                omp_policy=self.omp_policy, shared_budget=budget_authority,
            )
        except OmpPolicyError as exc:
            raise OmpServiceError(str(exc)) from exc
        packet_events = [
            event for event in self.ledger.events(parent["job_id"])
            if event.get("kind") == "omp_task_packet"
        ]
        if not packet_events:
            raise OmpServiceError("Prime task packet evidence is unavailable")
        allowed_value = packet_events[-1].get("data", {}).get("allowed_paths")
        if (
            not isinstance(allowed_value, list)
            or not 1 <= len(allowed_value) <= 8
            or any(not isinstance(path, str) or not path for path in allowed_value)
            or len(allowed_value) != len(set(allowed_value))
        ):
            raise OmpServiceError("Prime task packet allowed paths are malformed")
        allowed_paths = tuple(allowed_value)
        budget = budget_authority
        repair_turns = bounded_repair_turns(budget)
        repair_stop = budget.get("repair_stop_decision")
        lane_events = [event for event in self.ledger.events(parent["job_id"]) if event.get("kind") == "execution_lane"]
        actual_lane = str(lane_events[-1].get("data", {}).get("lane")) if lane_events else ""
        policy_events = [
            event for event in self.ledger.events(parent["job_id"])
            if event.get("kind") == "omp_policy_bound"
        ]
        preset_digest = str(policy_events[-1].get("data", {}).get("preset_digest", "")) if policy_events else ""
        if not re.fullmatch(r"[0-9a-f]{64}", preset_digest):
            raise OmpServiceError("durable OMP preset authority is unavailable")
        capability_digest = hashlib.sha256(context["capability_receipt"].encode()).hexdigest()
        capability_events = [
            event for event in self.ledger.events(parent["job_id"])
            if event.get("kind") == "capability_authorized"
            and event.get("data", {}).get("request_id") == context.get("request_id")
            and event.get("data", {}).get("alias") == "omp.worker.run"
        ]
        if not capability_events or capability_events[-1].get("data", {}).get("capability_digest") != capability_digest:
            raise OmpServiceError("OMP capability authority evidence is unavailable")
        admission = {**admission, "capability_digest": capability_digest}
        initial_record = {
            "admission": admission,
            "capability_digest": capability_digest,
            "prompt_hash": hashlib.sha256(arguments["prompt"].encode()).hexdigest(),
            "requested_profile": context["profile"],
            "resolved_provider": route["provider"],
            "resolved_model": route["model"],
            "route_fingerprint": route["fingerprint"],
            "workspace_scope": [str(workspace)],
            "actual_lane": actual_lane,
            "preset_digest": preset_digest,
            "capability_request_id": context["request_id"],
        }
        child = self.ledger.create_child(
            parent_job_id=parent["job_id"], kind="omp_worker", record=initial_record,
        )
        child_id = child["child_job_id"]
        try:
            self.ledger.update_child(child_id, status="running", record=initial_record)
            worktree = OmpWorktree(workspace, allowed_paths=allowed_paths)
            worker = OmpWorker(
                parent_prime_job_id=parent["job_id"], child_omp_job_id=child_id,
                project_root=workspace, worktree=worktree, test_policy=self.test_policy,
                model_route_fingerprint=route["fingerprint"], capability_receipt=context["capability_receipt"],
                allowed_paths=allowed_paths, profile=context["profile"], provider_id=route["provider"], model_id=route["model"],
                proposal_runner=OmpSdkSidecarRunner.from_policy(self.root, self.image,
                    max_tool_calls=budget.get("tool_calls"), max_wall_seconds=budget.get("wall_seconds")),
                max_repair_turns=repair_turns,
                repair_guard=(lambda _attempt, _feedback: dict(repair_stop)) if isinstance(repair_stop, dict) else None,
            )
            result = await worker.start(prompt=arguments["prompt"])
            patch = result.get("patch")
            patch_guard = assess_patch_integrity(patch) if result["status"] == "completed" else None
            if patch_guard is not None and patch_guard["status"] != "accepted":
                result = {**result, "status": "failed", "error": patch_guard["reason"]}
            record = {
                **initial_record, "status": result["status"],
                "patch_hash": patch.get("patch_hash") if isinstance(patch, dict) else None,
                "changed_paths": patch.get("changed_paths", []) if isinstance(patch, dict) else [],
                "patch_guard": patch_guard,
                "sdk_evidence": result.get("sdk_evidence", {}),
                "error": result.get("error"), "error_code": result.get("error_code"),
            }
            status = "completed" if result["status"] == "completed" else result["status"]
            self.ledger.update_child(child_id, status=status, record=record)
            if status == "completed":
                self._results[child_id] = result
            return {
                "child_omp_job_id": child_id, "status": status,
                "patch": {"patch_hash": patch.get("patch_hash")} if isinstance(patch, dict) else None,
                "sdk_evidence": result.get("sdk_evidence", {}), "error": result.get("error"),
                "error_code": result.get("error_code"),
                "capability_receipt": context["capability_receipt"],
                "requested_profile": context["profile"], "resolved_provider": route["provider"],
                "resolved_model": route["model"], "route_fingerprint": route["fingerprint"],
            }
        except BaseException as exc:
            terminal = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            current = self.ledger.child(child_id)
            if current["status"] not in {"completed", "failed", "cancelled"}:
                self.ledger.update_child(
                    child_id, status=terminal,
                    record={**initial_record, "error": type(exc).__name__, "reconciled": True},
                )
            raise
        finally:
            self.ledger.release_child_reservation(child_id)
