"""Sibling-container launch specification and Hermes model relay for OMP."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from prime_bridge.omp_policy import OmpPolicyError


class OmpRuntimeError(RuntimeError):
    pass


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class LspServer:
    language: str
    executable: str
    version: str
    digest: str
    reviewed: bool
    read_only: bool
    deny: tuple[str, ...]


@dataclass(frozen=True)
class LspRegistry:
    enabled: bool
    servers: Mapping[str, LspServer]
    deny_public_egress: bool
    deny_dynamic_download: bool
    deny_lspmux_auto_detection: bool
    deny_raw_lsp_requests: bool
    reject_server_initiated_edits: bool

    @classmethod
    def load(cls, path: Path) -> "LspRegistry":
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OmpRuntimeError("LSP registry is unavailable") from exc
        expected = {
            "schema_version", "enabled", "servers", "deny_public_egress",
            "deny_dynamic_download", "deny_lspmux_auto_detection",
            "deny_raw_lsp_requests", "reject_server_initiated_edits",
        }
        if not isinstance(value, dict) or set(value) != expected or value.get("schema_version") != "1.0":
            raise OmpRuntimeError("LSP registry keys are not closed")
        if type(value["enabled"]) is not bool:
            raise OmpRuntimeError("LSP registry enabled flag is invalid")
        for flag in ("deny_public_egress", "deny_dynamic_download", "deny_lspmux_auto_detection", "deny_raw_lsp_requests", "reject_server_initiated_edits"):
            if value[flag] is not True:
                raise OmpRuntimeError(f"LSP {flag} must be enforced")
        servers: dict[str, LspServer] = {}
        for name, item in value["servers"].items():
            if not isinstance(item, dict) or set(item) != {"language", "executable", "version", "digest", "reviewed", "read_only", "deny"}:
                raise OmpRuntimeError("LSP server entry is invalid")
            if item["reviewed"] is not True or item["read_only"] is not True:
                raise OmpRuntimeError("LSP server must be reviewed and read-only")
            if not _DIGEST.fullmatch(item["digest"]):
                raise OmpRuntimeError("LSP server digest is not pinned")
            deny = tuple(item["deny"])
            if "workspace/applyEdit" not in deny:
                raise OmpRuntimeError("LSP server must deny workspace/applyEdit")
            servers[name] = LspServer(
                language=item["language"], executable=item["executable"],
                version=item["version"], digest=item["digest"],
                reviewed=True, read_only=True, deny=deny,
            )
        return cls(
            enabled=value["enabled"], servers=servers,
            deny_public_egress=True, deny_dynamic_download=True,
            deny_lspmux_auto_detection=True, deny_raw_lsp_requests=True,
            reject_server_initiated_edits=True,
        )

    def server_for(self, language: str) -> LspServer:
        if not self.enabled:
            raise OmpRuntimeError("LSP is disabled")
        for server in self.servers.values():
            if server.language == language:
                return server
        raise OmpRuntimeError(f"no reviewed LSP server for language: {language}")


def load_runtime_lock(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OmpRuntimeError("OMP runtime lock is unavailable") from exc
    if value.get("locked") is not True:
        raise OmpRuntimeError("OMP runtime lock is not locked")
    if value.get("package", {}).get("version") != "17.3.0":
        raise OmpRuntimeError("OMP package version is not pinned")
    if not _DIGEST.fullmatch(value.get("image", {}).get("digest", "")):
        raise OmpRuntimeError("OMP image digest is not pinned")
    if not _HEX64.fullmatch(value.get("image", {}).get("sbom_sha256", "")):
        raise OmpRuntimeError("OMP SBOM hash is not pinned")
    if not _HEX40.fullmatch(value.get("source", {}).get("commit", "")):
        raise OmpRuntimeError("OMP source commit is not pinned")
    return value


def sibling_container_spec(
    *,
    runtime_lock: Mapping[str, Any],
    project_root: Path,
    worktree: Path,
    output_dir: Path,
    config_dir: Path,
    model_route: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the exact sibling-container launch specification (fail closed)."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise OmpRuntimeError("project root is not a directory")
    for label, path in (("worktree", worktree), ("output", output_dir), ("config", config_dir)):
        resolved = Path(path).resolve()
        if not resolved.is_dir():
            raise OmpRuntimeError(f"{label} directory is required")
    if not isinstance(model_route, Mapping) or model_route.get("status") != "READY":
        raise OmpRuntimeError("Hermes model route is not READY")
    if model_route.get("explicit_fallback") is not None:
        raise OmpRuntimeError("OMP must not use an explicit model fallback")
    image = runtime_lock["image"]["digest"]
    return {
        "schema_version": "1.0",
        "image": image,
        "image_tag": runtime_lock["image"].get("tag", ""),
        "mounts": {
            "source": {"path": str(root), "mode": "ro"},
            "worktree": {"path": str(Path(worktree).resolve()), "mode": "rw"},
            "output": {"path": str(Path(output_dir).resolve()), "mode": "rw"},
            "config": {"path": str(Path(config_dir).resolve()), "mode": "rw"},
        },
        "network": "none",
        "privileged": False,
        "read_only_rootfs": True,
        "no_podman_socket": True,
        "no_device_mounts": True,
        "no_host_pid_ipc": True,
        "model_route_fingerprint": model_route.get("route", {}).get("fingerprint"),
    }


def model_route_compatible(model_route: Mapping[str, Any]) -> dict[str, Any]:
    """Assert the Hermes/Ollama route supports the OMP SDK requirements."""
    if not isinstance(model_route, Mapping) or model_route.get("status") != "READY":
        return {"compatible": False, "reason": "model_route_not_ready"}
    compatibility = model_route.get("compatibility", {})
    required = {
        "supports_tools": compatibility.get("supports_tools"),
        "supports_streaming": compatibility.get("supports_streaming"),
    }
    if required["supports_tools"] is not True or required["supports_streaming"] is not True:
        return {"compatible": False, "reason": "model_route_missing_tool_or_stream_support"}
    if model_route.get("explicit_fallback") is not None:
        return {"compatible": False, "reason": "model_route_has_fallback"}
    return {"compatible": True, "reason": "model_route_compatible"}
