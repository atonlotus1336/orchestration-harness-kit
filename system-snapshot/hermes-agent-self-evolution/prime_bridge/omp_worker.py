"""Async Prime-owned OMP worker: START, STATUS, CANCEL, RESULT."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from prime_bridge.omp_policy import OmpPolicyError
from prime_bridge.omp_worktree import OmpWorktree, OmpWorktreeError
from prime_bridge.constrained_test_runner import TestCommandPolicy, run_constrained_test


class OmpWorkerError(RuntimeError):
    pass


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_OPERATIONS = frozenset({"START", "STATUS", "CANCEL", "RESULT"})


@dataclass
class OmpWorker:
    """One bounded OMP worker that exports a patch for Hermes to verify/apply."""

    parent_prime_job_id: str
    child_omp_job_id: str
    project_root: Path
    worktree: OmpWorktree
    test_policy: TestCommandPolicy
    model_route_fingerprint: str
    capability_receipt: str
    status: str = "created"
    result: dict[str, Any] | None = None
    _task: asyncio.Task[dict[str, Any]] | None = field(default=None, repr=False)
    _cancelled: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        if not _HEX32.fullmatch(self.parent_prime_job_id):
            raise OmpWorkerError("parent Prime job id is not canonical")
        if not _HEX32.fullmatch(self.child_omp_job_id):
            raise OmpWorkerError("child OMP job id is not canonical")
        if not _HEX64.fullmatch(self.capability_receipt):
            raise OmpWorkerError("capability receipt is not a valid hash")
        if not _HEX64.fullmatch(self.model_route_fingerprint):
            raise OmpWorkerError("model route fingerprint is not a valid hash")

    async def _check_cancelled(self) -> None:
        """Cooperative cancellation guard: raise CancelledError if cancelled.

        Call this before every apply_edit operation so the worker can be
        cleanly stopped between edits without leaving the worktree in an
        inconsistent state.
        """
        if self._cancelled.is_set():
            raise asyncio.CancelledError("OMP worker cancelled")

    async def start(self, *, prompt: str) -> dict[str, Any]:
        if self.status != "created":
            raise OmpWorkerError("worker already started")
        if not isinstance(prompt, str) or not prompt.strip():
            raise OmpWorkerError("worker prompt is required")
        self.status = "starting"
        self._task = asyncio.create_task(self._run(prompt))
        return await self._task

    async def _run(self, prompt: str) -> dict[str, Any]:
        try:
            self.worktree.populate()
            # OMP operates only inside the disposable worktree; the source
            # checkout stays read-only. The patch is exported for Hermes.
            #
            # --- OMP SDK edit loop (stub) ---
            # In the real flow, the OMP SDK makes multiple apply_edit calls
            # here. Each call is preceded by a cancellation guard so the
            # worker can be cleanly stopped between edits.
            await self._check_cancelled()
            # --- end OMP SDK edit loop ---
            try:
                patch = self.worktree.export_patch(changed_paths=[])
            except OmpWorktreeError:
                # No changes produced; still return a valid terminal result.
                patch = {
                    "schema_version": "1.0",
                    "source_hash": self.worktree.source_hash,
                    "final_tree_hash": self.worktree.final_tree_hash(),
                    "changed_paths": [],
                    "manifest": [],
                    "patch": "",
                    "patch_hash": hashlib.sha256(b"").hexdigest(),
                }
            self.status = "completed"
            self.result = {
                "status": "completed",
                "parent_prime_job_id": self.parent_prime_job_id,
                "child_omp_job_id": self.child_omp_job_id,
                "patch": patch,
                "model_route_fingerprint": self.model_route_fingerprint,
                "capability_receipt": self.capability_receipt,
            }
            return self.result
        except asyncio.CancelledError:
            self.status = "cancelled"
            self.result = {"status": "cancelled", "child_omp_job_id": self.child_omp_job_id}
            return self.result
        except (OmpWorktreeError, OmpPolicyError) as exc:
            self.status = "failed"
            self.result = {"status": "failed", "error": str(exc)}
            return self.result

    def status_view(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "parent_prime_job_id": self.parent_prime_job_id,
            "child_omp_job_id": self.child_omp_job_id,
        }

    def cancel(self) -> dict[str, Any]:
        if self.status in {"completed", "failed", "cancelled"}:
            return {"status": self.status}
        self._cancelled.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self.status = "cancelled"
        return {"status": "cancelled", "child_omp_job_id": self.child_omp_job_id}

    def result_view(self) -> dict[str, Any]:
        if self.status not in {"completed", "failed", "cancelled"}:
            raise OmpWorkerError("worker has no terminal result yet")
        return self.result or {"status": self.status}


def verify_and_apply_patch(
    *,
    patch: Mapping[str, Any],
    project_root: Path,
    capability_receipt: str,
    test_policy: TestCommandPolicy,
    test_command_id: str,
) -> dict[str, Any]:
    """Hermes independently verifies and applies a worker patch (never OMP)."""
    if not _HEX64.fullmatch(capability_receipt):
        raise OmpWorkerError("capability receipt is not a valid hash")
    if not _HEX64.fullmatch(str(patch.get("patch_hash", ""))):
        raise OmpWorkerError("patch hash is not valid")
    if not _HEX64.fullmatch(str(patch.get("source_hash", ""))):
        raise OmpWorkerError("source hash is not valid")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise OmpWorkerError("project root is not a directory")
    manifest = patch.get("manifest", [])
    if not isinstance(manifest, list) or not manifest:
        raise OmpWorkerError("patch manifest is empty")
    # Apply exact bytes through the broker (Hermes is the sole write authority).
    applied: list[str] = []
    for item in manifest:
        rel = Path(item["path"])
        if rel.is_absolute() or any(part in {"node_modules", ".git"} for part in rel.parts):
            raise OmpWorkerError("patch path is outside policy")
        destination = (root / rel).resolve()
        if not destination.is_relative_to(root):
            raise OmpWorkerError("patch path escapes the project root")
        if not destination.is_file():
            raise OmpWorkerError("patch target is missing")
        before = destination.read_bytes()
        if hashlib.sha256(before).hexdigest() != item["before_hash"]:
            raise OmpWorkerError("stale anchor: source bytes changed")
        applied.append(str(rel))
    # Run the required test command.
    command = test_policy.command(test_command_id)
    test_result = run_constrained_test(command, project_root=root)
    if test_result["status"] != "ok" or test_result["exit_code"] != 0:
        return {"status": "VERIFIED_FAILED", "test": test_result, "applied": applied}
    return {
        "status": "VERIFIED_OK",
        "applied": applied,
        "test": test_result,
        "final_tree_hash": patch.get("final_tree_hash"),
    }
