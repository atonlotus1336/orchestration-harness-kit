"""Phase 10 Plan 05: OMP worker, worktree, constrained test runner, async supervision."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from prime_bridge.constrained_test_runner import TestCommandPolicy, TestRunnerError, run_constrained_test
from prime_bridge.job_ledger import JobLedger, LedgerError
from prime_bridge.omp_worker import OmpWorker, OmpWorkerError, verify_and_apply_patch
from prime_bridge.omp_worktree import OmpWorktree, OmpWorktreeError, source_tree_hash
from prime_bridge.supervisor import PrimeSupervisor, PrimePolicy

ROOT = Path(__file__).resolve().parents[2]
TEST_POLICY = TestCommandPolicy.load(ROOT / "config/omp-test-command-policy.json")


def _make_project(tmp_path):
    (tmp_path / "file.py").write_text("x = 1\n")
    (tmp_path / "test_file.py").write_text("def test_x():\n    assert True\n")
    return tmp_path


# --- Task 1: disposable worktree and typed patch result ---

def test_worktree_populates_and_hashes(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worktree.populate()
    assert (worktree.worktree / "file.py").is_file()
    assert worktree.source_hash == source_tree_hash(project)
    assert worktree.final_tree_hash() == worktree.source_hash


def test_worktree_export_patch_rejects_empty(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worktree.populate()
    with pytest.raises(OmpWorktreeError):
        worktree.export_patch(changed_paths=[])


def test_worktree_export_patch_hashes_changes(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worktree.populate()
    (worktree.worktree / "file.py").write_text("x = 2\n")
    patch = worktree.export_patch(changed_paths=["file.py"])
    assert patch["source_hash"] == worktree.source_hash
    assert patch["changed_paths"] == ["file.py"]
    assert len(patch["patch_hash"]) == 64
    assert patch["manifest"][0]["before_hash"] == hashlib.sha256(b"x = 1\n").hexdigest()


def test_worktree_rejects_patch_path_escape(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worktree.populate()
    with pytest.raises(OmpWorktreeError):
        worktree.export_patch(changed_paths=["../outside.py"])


# --- Task 2: async supervision and shared budget ---

def test_omp_worker_lifecycle(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worker = OmpWorker(
        parent_prime_job_id="a" * 32, child_omp_job_id="b" * 32,
        project_root=project, worktree=worktree, test_policy=TEST_POLICY,
        model_route_fingerprint="c" * 64, capability_receipt="d" * 64,
    )
    result = asyncio.run(worker.start(prompt="implement feature"))
    assert result["status"] == "completed"
    assert result["parent_prime_job_id"] == "a" * 32
    assert result["child_omp_job_id"] == "b" * 32
    assert "patch" in result


def test_omp_worker_cancel(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worker = OmpWorker(
        parent_prime_job_id="a" * 32, child_omp_job_id="b" * 32,
        project_root=project, worktree=worktree, test_policy=TEST_POLICY,
        model_route_fingerprint="c" * 64, capability_receipt="d" * 64,
    )
    # Cancel a worker that has not yet started -> transitions to cancelled.
    cancelled = worker.cancel()
    assert cancelled["status"] == "cancelled"


def test_omp_worker_cancellation_guard_raises(tmp_path):
    """_check_cancelled raises CancelledError when the event is set."""
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worker = OmpWorker(
        parent_prime_job_id="a" * 32, child_omp_job_id="b" * 32,
        project_root=project, worktree=worktree, test_policy=TEST_POLICY,
        model_route_fingerprint="c" * 64, capability_receipt="d" * 64,
    )
    worker._cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker._check_cancelled())


def test_omp_worker_cancel_sets_event(tmp_path):
    """cancel() sets the _cancelled event so cooperative guards fire."""
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worker = OmpWorker(
        parent_prime_job_id="a" * 32, child_omp_job_id="b" * 32,
        project_root=project, worktree=worktree, test_policy=TEST_POLICY,
        model_route_fingerprint="c" * 64, capability_receipt="d" * 64,
    )
    assert not worker._cancelled.is_set()
    worker.cancel()
    assert worker._cancelled.is_set()


def test_omp_worker_cancel_mid_run_returns_cancelled(tmp_path):
    """Cancel a running worker mid-edit: the guard catches it, result is cancelled."""
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worker = OmpWorker(
        parent_prime_job_id="a" * 32, child_omp_job_id="b" * 32,
        project_root=project, worktree=worktree, test_policy=TEST_POLICY,
        model_route_fingerprint="c" * 64, capability_receipt="d" * 64,
    )

    async def run_and_cancel():
        # Set the cancellation event before the task reaches the guard.
        # In the real flow, cancel() is called from another coroutine
        # while the worker is mid-edit; the guard catches it cooperatively.
        worker._cancelled.set()
        task = asyncio.create_task(worker._run("implement feature"))
        return await task

    result = asyncio.run(run_and_cancel())
    assert result["status"] == "cancelled"
    assert result["child_omp_job_id"] == "b" * 32


def test_omp_worker_rejects_bad_ids(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    with pytest.raises(OmpWorkerError):
        OmpWorker(
            parent_prime_job_id="bad", child_omp_job_id="b" * 32,
            project_root=project, worktree=worktree, test_policy=TEST_POLICY,
            model_route_fingerprint="c" * 64, capability_receipt="d" * 64,
        )


def _parent_route():
    route = {"provider": "ollama", "model": "m", "api": "openai-completions", "endpoint": "https://ollama.com/v1"}
    route["fingerprint"] = JobLedger.route_fingerprint(route)
    return route


def _parent_runtime():
    runtime = {"source_commit": "f" * 40, "image_digest": "sha256:" + "a" * 64, "sandbox": "rootless-podman", "command_fingerprint": "b" * 64}
    runtime["fingerprint"] = JobLedger.runtime_fingerprint(runtime)
    return runtime


def test_ledger_child_job_lifecycle(tmp_path):
    ledger = JobLedger(tmp_path / "ledger.db", [tmp_path])
    parent = ledger.create(
        owner="hermes", profile="workspace", project="proj", session_id="sess",
        workspace=tmp_path, route=_parent_route(),
        limits={"max_iterations": 5, "no_progress_seconds": 60, "wall_seconds": 300},
        runtime=_parent_runtime(),
    )
    child = ledger.create_child(parent_job_id=parent["job_id"], kind="omp_worker", record={"prompt": "x"})
    assert child["parent_job_id"] == parent["job_id"]
    assert child["kind"] == "omp_worker"
    updated = ledger.update_child(child["child_job_id"], status="completed", record={"result": "ok"})
    assert updated["status"] == "completed"
    assert ledger.children(parent["job_id"])[0]["child_job_id"] == child["child_job_id"]


def test_supervisor_omp_operations(tmp_path):
    ledger = JobLedger(tmp_path / "ledger.db", [tmp_path])
    supervisor = PrimeSupervisor(ledger, worker_factory=lambda job: None, policy=PrimePolicy())
    parent = ledger.create(
        owner="hermes", profile="workspace", project="proj", session_id="sess",
        workspace=tmp_path, route=_parent_route(),
        limits={"max_iterations": 5, "no_progress_seconds": 60, "wall_seconds": 300},
        runtime=_parent_runtime(),
    )
    ledger.transition(parent["job_id"], "starting")
    child = supervisor.submit_omp(parent["job_id"], kind="omp_worker", record={"prompt": "x"})
    assert supervisor.omp_status(child["child_job_id"])["status"] == "created"
    supervisor.omp_cancel(child["child_job_id"])
    assert supervisor.omp_status(child["child_job_id"])["status"] == "cancelled"


# --- Task 3: Hermes patch apply and constrained tests ---

def test_constrained_test_runner_runs_allowlisted_command(tmp_path):
    project = _make_project(tmp_path)
    # Use the venv python so pytest is available in the test project.
    policy = TestCommandPolicy.load(ROOT / "config/omp-test-command-policy.json")
    command = policy.command("python_compile")
    result = run_constrained_test(command, project_root=project)
    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert len(result["output_hash"]) == 64


def test_constrained_test_runner_rejects_unknown_command():
    with pytest.raises(TestRunnerError):
        TEST_POLICY.command("not-in-allowlist")


def test_verify_and_apply_patch_verifies_and_tests(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worktree.populate()
    (worktree.worktree / "file.py").write_text("x = 2\n")
    patch = worktree.export_patch(changed_paths=["file.py"])
    result = verify_and_apply_patch(
        patch=patch, project_root=project, capability_receipt="d" * 64,
        test_policy=TEST_POLICY, test_command_id="python_compile",
    )
    assert result["status"] == "VERIFIED_OK"
    assert result["applied"] == ["file.py"]


def test_verify_and_apply_patch_rejects_stale_anchor(tmp_path):
    project = _make_project(tmp_path)
    worktree = OmpWorktree(project)
    worktree.populate()
    (worktree.worktree / "file.py").write_text("x = 2\n")
    patch = worktree.export_patch(changed_paths=["file.py"])
    # Change the source after the patch was exported -> stale anchor.
    (project / "file.py").write_text("x = 3\n")
    with pytest.raises(OmpWorkerError):
        verify_and_apply_patch(
            patch=patch, project_root=project, capability_receipt="d" * 64,
            test_policy=TEST_POLICY, test_command_id="pytest",
        )
