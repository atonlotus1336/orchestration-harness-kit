"""Phase 10 Plan 03: OMP sidecar containment, model relay, and LSP registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prime_bridge.omp_runtime import (
    LspRegistry, OmpRuntimeError, load_runtime_lock, model_route_compatible,
    sibling_container_spec,
)

ROOT = Path(__file__).resolve().parents[2]
LSP = LspRegistry.load(ROOT / "config/omp-lsp-servers.json")
LOCK = load_runtime_lock(ROOT / "config/omp-runtime-lock.json")


def _model_route():
    return {
        "status": "READY",
        "compatibility": {"supports_tools": True, "supports_streaming": True},
        "explicit_fallback": None,
        "route": {"fingerprint": "b" * 64},
    }


# --- Task 1: sibling sidecar and zero-discovery configuration ---

def test_runtime_lock_is_locked_and_pinned():
    assert LOCK["locked"] is True
    assert LOCK["package"]["version"] == "17.3.0"
    assert LOCK["image"]["digest"].startswith("sha256:")
    assert len(LOCK["source"]["commit"]) == 40


def test_containment_sibling_container_spec_is_closed(tmp_path):
    worktree = tmp_path / "worktree"; worktree.mkdir()
    output = tmp_path / "output"; output.mkdir()
    config = tmp_path / "config"; config.mkdir()
    spec = sibling_container_spec(
        runtime_lock=LOCK, project_root=tmp_path, worktree=worktree,
        output_dir=output, config_dir=config, model_route=_model_route(),
    )
    assert spec["network"] == "none"
    assert spec["privileged"] is False
    assert spec["read_only_rootfs"] is True
    assert spec["no_podman_socket"] is True
    assert spec["no_device_mounts"] is True
    assert spec["no_host_pid_ipc"] is True
    assert spec["mounts"]["source"]["mode"] == "ro"
    assert spec["mounts"]["worktree"]["mode"] == "rw"


def test_containment_sibling_container_spec_rejects_model_fallback(tmp_path):
    worktree = tmp_path / "worktree"; worktree.mkdir()
    output = tmp_path / "output"; output.mkdir()
    config = tmp_path / "config"; config.mkdir()
    route = _model_route()
    route["explicit_fallback"] = {"provider": "other"}
    with pytest.raises(OmpRuntimeError):
        sibling_container_spec(
            runtime_lock=LOCK, project_root=tmp_path, worktree=worktree,
            output_dir=output, config_dir=config, model_route=route,
        )


def test_containment_sibling_container_spec_requires_dirs(tmp_path):
    with pytest.raises(OmpRuntimeError):
        sibling_container_spec(
            runtime_lock=LOCK, project_root=tmp_path,
            worktree=tmp_path / "missing", output_dir=tmp_path / "missing",
            config_dir=tmp_path / "missing", model_route=_model_route(),
        )


# --- Task 2: Hermes model relay and reviewed LSP registry ---

def test_model_route_compatible_accepts_tool_and_stream():
    assert model_route_compatible(_model_route())["compatible"] is True


def test_model_route_compatible_rejects_missing_tools():
    route = _model_route()
    route["compatibility"] = {"supports_tools": False, "supports_streaming": True}
    assert model_route_compatible(route)["compatible"] is False


def test_model_route_compatible_rejects_fallback():
    route = _model_route()
    route["explicit_fallback"] = {"provider": "other"}
    assert model_route_compatible(route)["compatible"] is False


def test_lsp_registry_requires_reviewed_read_only_servers():
    for server in LSP.servers.values():
        assert server.reviewed is True
        assert server.read_only is True
        assert "workspace/applyEdit" in server.deny


def test_discovery_lsp_registry_denies_unknown_language():
    with pytest.raises(OmpRuntimeError):
        LSP.server_for("unknown-language")


def test_lsp_registry_denies_apply_edit():
    assert LSP.reject_server_initiated_edits is True
    assert LSP.deny_public_egress is True
    assert LSP.deny_dynamic_download is True
    assert LSP.deny_lspmux_auto_detection is True
    assert LSP.deny_raw_lsp_requests is True
