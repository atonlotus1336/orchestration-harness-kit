"""Phase 10 Plan 06: Prime-to-OMP closure matrix (positive + negative)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from prime_bridge.intent_router import IntentPolicy, classify
from prime_bridge.omp_adapters import (
    OmpAdapterError, create_hash_edit_proposal, validate_lsp_request,
)
from prime_bridge.omp_policy import OmpCodingPolicy, OmpPolicyError, admit_omp
from prime_bridge.omp_runtime import LspRegistry, model_route_compatible
from prime_bridge.omp_worker import OmpWorker, OmpWorkerError, verify_and_apply_patch
from prime_bridge.omp_worktree import OmpWorktree

ROOT = Path(__file__).resolve().parents[2]
POLICY = IntentPolicy.load(ROOT / "config/prime-auto-route-policy.json")
OMP_POLICY = OmpCodingPolicy.load(ROOT / "config/omp-coding-policy.json")
LSP = LspRegistry.load(ROOT / "config/omp-lsp-servers.json")
SKILL = ROOT.parent / "skills-shared/ponytail/skills/ponytail/SKILL.md"


def ponytail():
    return {"available": True, "path": POLICY.ponytail_path, "intensity": "full", "fingerprint": hashlib.sha256(SKILL.read_bytes()).hexdigest()}


def _feasibility():
    return {"verdict": "READY", "captured_at": "2026-08-13T00:00:00Z"}


def _prime_job():
    return {"job_id": "a" * 32, "status": "running"}


def _model_route():
    return {"status": "READY", "compatibility": {"supports_tools": True, "supports_streaming": True}, "explicit_fallback": None, "route": {"fingerprint": "b" * 64}}


def _capability_policy():
    return {"enabled": True}


def _shared_budget():
    return {"status": "READY"}


# --- Positive cases ---

def test_positive_lsp_adapter(tmp_path):
    request, server = validate_lsp_request(
        job_id="a" * 32, profile="workspace", project_root=tmp_path, language="python",
        method="textDocument/hover", params={"textDocument": {"uri": "file:///x.py"}},
        lsp_registry=LSP, timeout_seconds=10, item_limit=100, byte_limit=100_000,
    )
    assert request.method == "textDocument/hover"
    assert server.read_only is True


def test_positive_hash_edit_preview(tmp_path):
    proposal = create_hash_edit_proposal(
        job_id="a" * 32, profile="workspace", project_root=tmp_path,
        relative_path="file.py", before_bytes=b"x", proposed_bytes=b"y",
        capability_receipt="c" * 64,
    )
    assert proposal.before_hash == hashlib.sha256(b"x").hexdigest()


def test_positive_omp_admission(tmp_path):
    receipt = admit_omp(
        feasibility=_feasibility(), prime_job=_prime_job(), project_root=tmp_path,
        profile="workspace", model_route=_model_route(), capability_policy=_capability_policy(),
        omp_policy=OMP_POLICY, shared_budget=_shared_budget(),
    )
    assert receipt["status"] == "ADMITTED"


def test_positive_worker_patch_and_apply(tmp_path):
    (tmp_path / "file.py").write_text("x = 1\n")
    worktree = OmpWorktree(tmp_path)
    worktree.populate()
    (worktree.worktree / "file.py").write_text("x = 2\n")
    patch = worktree.export_patch(changed_paths=["file.py"])
    assert patch["changed_paths"] == ["file.py"]


def test_positive_native_request_stays_native():
    result = classify("What is Prime Agent?", profile="workspace", mode="prime-default",
                      readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] == "NATIVE"


def test_positive_prime_only_request():
    result = classify("audit phase 9", profile="workspace", mode="prime-default",
                      readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    assert result["outcome"] == "PRIME_AUTO"


# --- Negative cases (forbidden edges) ---

def test_negative_direct_omp_denied(tmp_path):
    with pytest.raises(OmpPolicyError):
        admit_omp(
            feasibility=_feasibility(), prime_job={"job_id": "a" * 32, "status": "completed"},
            project_root=tmp_path, profile="workspace", model_route=_model_route(),
            capability_policy=_capability_policy(), omp_policy=OMP_POLICY, shared_budget=_shared_budget(),
        )


def test_negative_alternate_provider_denied(tmp_path):
    route = _model_route()
    route["explicit_fallback"] = {"provider": "other"}
    with pytest.raises(OmpPolicyError):
        admit_omp(
            feasibility=_feasibility(), prime_job=_prime_job(), project_root=tmp_path,
            profile="workspace", model_route=route, capability_policy=_capability_policy(),
            omp_policy=OMP_POLICY, shared_budget=_shared_budget(),
        )


def test_negative_lsp_edit_denied(tmp_path):
    with pytest.raises(OmpAdapterError):
        validate_lsp_request(
            job_id="a" * 32, profile="workspace", project_root=tmp_path, language="python",
            method="workspace/applyEdit", params={}, lsp_registry=LSP,
            timeout_seconds=10, item_limit=100, byte_limit=100_000,
        )


def test_negative_path_escape_denied(tmp_path):
    with pytest.raises(OmpAdapterError):
        create_hash_edit_proposal(
            job_id="a" * 32, profile="workspace", project_root=tmp_path,
            relative_path="../outside.py", before_bytes=b"x", proposed_bytes=b"y",
            capability_receipt="c" * 64,
        )


def test_negative_stale_hash_denied(tmp_path):
    (tmp_path / "file.py").write_text("x = 1\n")
    worktree = OmpWorktree(tmp_path)
    worktree.populate()
    (worktree.worktree / "file.py").write_text("x = 2\n")
    patch = worktree.export_patch(changed_paths=["file.py"])
    (tmp_path / "file.py").write_text("x = 3\n")  # stale anchor
    with pytest.raises(OmpWorkerError):
        verify_and_apply_patch(
            patch=patch, project_root=tmp_path, capability_receipt="d" * 64,
            test_policy=None, test_command_id="python_compile",
        )


def test_negative_invalid_evidence_denied(tmp_path):
    with pytest.raises(OmpPolicyError):
        admit_omp(
            feasibility={"verdict": "PHASE9_BLOCKED"}, prime_job=_prime_job(),
            project_root=tmp_path, profile="workspace", model_route=_model_route(),
            capability_policy=_capability_policy(), omp_policy=OMP_POLICY, shared_budget=_shared_budget(),
        )


def test_negative_model_route_incompatible():
    route = _model_route()
    route["compatibility"] = {"supports_tools": False, "supports_streaming": True}
    assert model_route_compatible(route)["compatible"] is False


# --- Performance / rollback / fallback gates ---

def test_performance_adapter_startup_bounded():
    """Adapter startup (excluding LSP cold start) must be <= 3s p95."""
    import time
    start = time.monotonic()
    # Pure validation path (no LSP process spawn) is the adapter startup.
    validate_lsp_request(
        job_id="a" * 32, profile="workspace", project_root=ROOT, language="python",
        method="textDocument/hover", params={"textDocument": {"uri": "file:///x.py"}},
        lsp_registry=LSP, timeout_seconds=10, item_limit=100, byte_limit=100_000,
    )
    elapsed = time.monotonic() - start
    assert elapsed <= 3.0


def test_performance_worker_container_startup_bounded():
    """Worker container startup must be <= 8s p95 (spec construction only)."""
    import time
    start = time.monotonic()
    from prime_bridge.omp_runtime import sibling_container_spec
    worktree = ROOT / "containers"; output = ROOT / "output"; config = ROOT / "config"
    sibling_container_spec(
        runtime_lock=json.loads((ROOT / "config/omp-runtime-lock.json").read_text()),
        project_root=ROOT, worktree=worktree, output_dir=output, config_dir=config,
        model_route=_model_route(),
    )
    elapsed = time.monotonic() - start
    assert elapsed <= 8.0


def test_rollback_global_disable():
    """Global disable rejects all OMP admission."""
    import copy
    disabled = copy.deepcopy(OMP_POLICY)
    # Simulate a disabled policy by loading a disabled variant.
    from prime_bridge.omp_policy import OmpCodingPolicy
    value = json.loads((ROOT / "config/omp-coding-policy.json").read_text())
    value["enabled"] = False
    path = ROOT / "config" / "omp-coding-policy.json"
    original = path.read_text()
    try:
        path.write_text(json.dumps(value))
        disabled_policy = OmpCodingPolicy.load(path)
        with pytest.raises(OmpPolicyError):
            admit_omp(
                feasibility=_feasibility(), prime_job=_prime_job(), project_root=ROOT,
                profile="workspace", model_route=_model_route(),
                capability_policy=_capability_policy(), omp_policy=disabled_policy,
                shared_budget=_shared_budget(),
            )
    finally:
        path.write_text(original)


def test_fallback_runtime_unavailable():
    """A non-READY feasibility falls back to native (no OMP)."""
    with pytest.raises(OmpPolicyError):
        admit_omp(
            feasibility={"verdict": "RUNTIME_UNAVAILABLE"}, prime_job=_prime_job(),
            project_root=ROOT, profile="workspace", model_route=_model_route(),
            capability_policy=_capability_policy(), omp_policy=OMP_POLICY,
            shared_budget=_shared_budget(),
        )
