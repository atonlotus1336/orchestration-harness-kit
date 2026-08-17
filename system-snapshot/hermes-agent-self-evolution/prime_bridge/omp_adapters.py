"""Narrow Prime-owned OMP capability adapters: read-only LSP and hash-edit preview."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from prime_bridge.omp_policy import OmpPolicyError
from prime_bridge.omp_runtime import LspRegistry, LspServer, OmpRuntimeError


class OmpAdapterError(RuntimeError):
    pass


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_EDIT_EXPIRY = timedelta(minutes=15)

# Read-only LSP methods only. Mutating/raw methods are rejected.
LSP_READ_METHODS = frozenset({
    "textDocument/diagnostic", "textDocument/definition", "textDocument/references",
    "textDocument/documentSymbol", "workspace/symbol", "textDocument/hover",
    "textDocument/capabilities",
})
LSP_MUTATION_METHODS = frozenset({
    "workspace/applyEdit", "textDocument/rename", "textDocument/formatting",
    "textDocument/codeAction", "textDocument/typeDefinition", "textDocument/implementation",
})


@dataclass(frozen=True)
class LspAdapterRequest:
    job_id: str
    profile: str
    project_root: Path
    language: str
    method: str
    params: Mapping[str, Any]
    timeout_seconds: int
    item_limit: int
    byte_limit: int
    cancellation_token: str | None = None


def _require_hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise OmpAdapterError(f"{label} is not a valid hash")
    return value


def _normalize_relative(project_root: Path, raw_path: str) -> str:
    """Normalize a path relative to the project root; reject escapes."""
    root = Path(project_root).resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise OmpAdapterError("absolute paths are forbidden")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise OmpAdapterError("path escapes the project root")
    return str(resolved.relative_to(root))


def validate_lsp_request(
    *,
    job_id: str,
    profile: str,
    project_root: Path,
    language: str,
    method: str,
    params: Mapping[str, Any],
    lsp_registry: LspRegistry,
    timeout_seconds: int,
    item_limit: int,
    byte_limit: int,
    cancellation_token: str | None = None,
) -> tuple[LspAdapterRequest, LspServer]:
    """Validate an LSP read request; returns the request and the reviewed server."""
    if not _PROFILE.fullmatch(profile):
        raise OmpAdapterError("profile is not canonical")
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise OmpAdapterError("job id is not canonical")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise OmpAdapterError("timeout must be between 1 and 60 seconds")
    if type(item_limit) is not int or not 1 <= item_limit <= 500:
        raise OmpAdapterError("item limit must be between 1 and 500")
    if type(byte_limit) is not int or not 1 <= byte_limit <= 1_000_000:
        raise OmpAdapterError("byte limit must be between 1 and 1,000,000")
    if method not in LSP_READ_METHODS:
        raise OmpAdapterError("LSP method is not read-only")
    if method in LSP_MUTATION_METHODS:
        raise OmpAdapterError("LSP mutation method is forbidden")
    try:
        server = lsp_registry.server_for(language)
    except OmpRuntimeError as exc:
        raise OmpAdapterError(str(exc)) from exc
    if not isinstance(params, Mapping):
        raise OmpAdapterError("LSP params must be an object")
    # Reject raw requests and server-initiated edits.
    if "raw" in params or "applyEdit" in str(params):
        raise OmpAdapterError("raw LSP request is forbidden")
    # Reject absolute or escaping paths in params.
    for key, value in params.items():
        if key in {"path", "uri", "file"} and isinstance(value, str):
            if value.startswith("/") or value.startswith("file:///") or ".." in value:
                raise OmpAdapterError("LSP path escapes the project root")
    request = LspAdapterRequest(
        job_id=job_id, profile=profile, project_root=Path(project_root).resolve(),
        language=language, method=method, params=dict(params),
        timeout_seconds=timeout_seconds, item_limit=item_limit,
        byte_limit=byte_limit, cancellation_token=cancellation_token,
    )
    return request, server


def sanitize_lsp_result(result: Any, *, byte_limit: int) -> dict[str, Any]:
    """Bound and redact an LSP result; returns a hashable summary."""
    def clean(value: Any, key: str | None = None) -> Any:
        if key is not None and re.search(r"(?:api.?key|authorization|credential|password|secret|token)", str(key), re.IGNORECASE):
            return "[redacted]"
        if isinstance(value, dict):
            return {str(child_key): clean(child, str(child_key)) for child_key, child in value.items()}
        if isinstance(value, list):
            return [clean(child) for child in value]
        if isinstance(value, str) and re.search(r"(?:-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}\b|\bAKIA[0-9A-Z]{16}\b)", value):
            return "[redacted]"
        return value
    sanitized = clean(result)
    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > byte_limit:
        return {"status": "truncated", "bytes": len(encoded), "result_hash": hashlib.sha256(encoded).hexdigest()}
    return {"status": "ok", "bytes": len(encoded), "result_hash": hashlib.sha256(encoded).hexdigest(), "result": sanitized}


@dataclass(frozen=True)
class HashEditProposal:
    job_id: str
    profile: str
    project_root: Path
    relative_path: str
    before_hash: str
    proposed_bytes_hash: str
    diff_hash: str
    expiry: str
    capability_receipt: str


def create_hash_edit_proposal(
    *,
    job_id: str,
    profile: str,
    project_root: Path,
    relative_path: str,
    before_bytes: bytes,
    proposed_bytes: bytes,
    capability_receipt: str,
    fuzzy: bool = False,
) -> HashEditProposal:
    """Create a PREVIEW-only hash-edit proposal. Never writes the authoritative repo."""
    if fuzzy:
        raise OmpAdapterError("fuzzy matching must be disabled")
    if not _PROFILE.fullmatch(profile):
        raise OmpAdapterError("profile is not canonical")
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise OmpAdapterError("job id is not canonical")
    _require_hex(capability_receipt, "capability receipt")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise OmpAdapterError("project root is not a directory")
    relative = _normalize_relative(root, relative_path)
    if any(part in {"node_modules", ".git", "lockfile", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"} for part in Path(relative).parts):
        raise OmpAdapterError("generated/lockfile path is forbidden")
    if Path(relative).is_symlink():
        raise OmpAdapterError("symlink path is forbidden")
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    proposed_hash = hashlib.sha256(proposed_bytes).hexdigest()
    diff = _unified_diff(relative, before_bytes, proposed_bytes)
    diff_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    expiry = (datetime.now(timezone.utc) + _EDIT_EXPIRY).isoformat()
    return HashEditProposal(
        job_id=job_id, profile=profile, project_root=root, relative_path=relative,
        before_hash=before_hash, proposed_bytes_hash=proposed_hash, diff_hash=diff_hash,
        expiry=expiry, capability_receipt=capability_receipt,
    )


def _unified_diff(relative_path: str, before: bytes, after: bytes) -> str:
    """Minimal unified diff for the proposal (not an authoritative write)."""
    before_lines = before.decode("utf-8", errors="replace").splitlines(keepends=True)
    after_lines = after.decode("utf-8", errors="replace").splitlines(keepends=True)
    header = f"--- a/{relative_path}\n+++ b/{relative_path}\n"
    if before_lines == after_lines:
        return header + "@@ -0,0 +0,0 @@\n"
    # Simple full-file diff for the preview; Hermes applies exact bytes later.
    return header + "@@ -1,{b} +1,{a} @@\n".format(b=len(before_lines), a=len(after_lines)) + "".join(after_lines)


def proposal_public(proposal: HashEditProposal) -> dict[str, Any]:
    """Return the secret-safe public proposal record."""
    return {
        "status": "preview",
        "job_id": proposal.job_id,
        "profile": proposal.profile,
        "project_root": str(proposal.project_root),
        "relative_path": proposal.relative_path,
        "before_hash": proposal.before_hash,
        "proposed_bytes_hash": proposal.proposed_bytes_hash,
        "diff_hash": proposal.diff_hash,
        "expiry": proposal.expiry,
        "capability_receipt": proposal.capability_receipt,
        "authoritative_write": False,
    }
