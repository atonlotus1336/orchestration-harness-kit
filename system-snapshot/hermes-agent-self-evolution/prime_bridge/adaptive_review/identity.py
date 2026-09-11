"""Exact content identity for adaptive review scopes.

Wave 0 task 2: exact content identity. A scope is a set of explicitly
declared repository-relative paths. Identity is computed over the declared
path set and over committed/dirty snapshot content in a way that is
independent of path ordering and that never absorbs unrelated files.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Mapping, Sequence

# Deterministic exclusion sets for generated and vendored content.
GENERATED_MARKERS = frozenset({".planning", ".pytest_cache", ".mypy_cache"})
VENDOR_MARKERS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "vendor", "dist", "build"}
)


class ScopeError(ValueError):
    """Raised when a declared scope path is invalid or unsafe."""


def _normalize_path(path: str) -> PurePosixPath:
    if not isinstance(path, str) or not path:
        raise ScopeError(f"invalid scope path: {path!r}")
    p = PurePosixPath(path)
    if p.is_absolute():
        raise ScopeError(f"absolute path not allowed: {path!r}")
    if ".." in p.parts:
        raise ScopeError(f"traversal path not allowed: {path!r}")
    return p


def validate_scope_paths(paths: Sequence[str]) -> list[str]:
    """Validate declared paths and return them in canonical, de-duplicated form.

    Every path must be repository-relative, must not traverse outside the
    repository root, and must not be a generated or vendored path.
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in paths:
        p = _normalize_path(raw)
        if any(part in VENDOR_MARKERS for part in p.parts):
            raise ScopeError(f"vendored path not allowed: {str(p)!r}")
        if any(part in GENERATED_MARKERS for part in p.parts):
            raise ScopeError(f"generated path not allowed: {str(p)!r}")
        key = str(p)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def exclude_generated_vendor(paths: Sequence[str]) -> list[str]:
    """Filter out generated/vendor paths deterministically, preserving order."""
    result: list[str] = []
    for raw in paths:
        p = _normalize_path(raw)
        if any(part in VENDOR_MARKERS for part in p.parts):
            continue
        if any(part in GENERATED_MARKERS for part in p.parts):
            continue
        result.append(str(p))
    return result


def declared_path_set_sha256(paths: Sequence[str]) -> str:
    """Ordering-neutral SHA-256 of the declared scope path set.

    The digest depends only on the set of declared paths, never on the order
    in which they were supplied.
    """
    canonical = sorted(validate_scope_paths(paths))
    digest = hashlib.sha256()
    for path in canonical:
        digest.update(path.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _content_map_sha256(contents: Mapping[str, str]) -> str:
    """Ordering-neutral SHA-256 over a path -> content-hash mapping."""
    digest = hashlib.sha256()
    for path in sorted(contents):
        digest.update(path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(contents[path].encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


@dataclass(frozen=True)
class Snapshot:
    """A point-in-time view of a scope's file contents.

    ``committed`` maps declared path -> content hash for committed content;
    ``dirty`` maps declared path -> content hash for uncommitted changes.
    ``role`` distinguishes base (pre-change) from head (post-change) snapshots.
    """

    committed: Mapping[str, str] = field(default_factory=dict)
    dirty: Mapping[str, str] = field(default_factory=dict)
    role: str = "head"

    def content_identity(self) -> str:
        """SHA-256 distinguishing this snapshot's role and content."""
        digest = hashlib.sha256()
        digest.update(self.role.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_content_map_sha256(self.committed).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_content_map_sha256(self.dirty).encode("utf-8"))
        return digest.hexdigest()


def scope_snapshot(
    declared: Sequence[str],
    all_files: Mapping[str, str],
    role: str = "head",
) -> Snapshot:
    """Build a snapshot containing only declared scope paths.

    Unrelated files present in ``all_files`` are never absorbed: only paths
    that appear in the declared scope are retained.
    """
    allowed = set(validate_scope_paths(declared))
    committed = {path: h for path, h in all_files.items() if path in allowed}
    return Snapshot(committed=committed, dirty={}, role=role)
