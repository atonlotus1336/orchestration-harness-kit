"""Disposable worktree/overlay for the OMP worker (Hermes-owned)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from prime_bridge.omp_policy import OmpPolicyError


class OmpWorktreeError(RuntimeError):
    pass


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_PARTS = {"node_modules", ".git", "lockfile", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_tree_hash(project_root: Path) -> str:
    """Deterministic hash of the authoritative source tree (fail closed)."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise OmpWorktreeError("project root is not a directory")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


class OmpWorktree:
    """An ephemeral writable overlay over a read-only source tree."""

    def __init__(self, source_root: Path, *, base_dir: Path | None = None):
        self.source_root = Path(source_root).resolve()
        if not self.source_root.is_dir():
            raise OmpWorktreeError("source root is not a directory")
        self.source_hash = source_tree_hash(self.source_root)
        self._temporary = tempfile.TemporaryDirectory(prefix="omp-worktree-", dir=str(base_dir) if base_dir else None)
        self.worktree = Path(self._temporary.name)
        self._populated = False

    def populate(self) -> None:
        """Copy the source tree into the writable overlay (read-only source)."""
        if self._populated:
            return
        for path in sorted(self.source_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.source_root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            destination = self.worktree / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        self._populated = True

    def final_tree_hash(self) -> str:
        """Hash the worktree after OMP edits (for patch verification)."""
        return source_tree_hash(self.worktree)

    def export_patch(self, *, changed_paths: list[str]) -> dict[str, Any]:
        """Export a unified patch, manifest, and before/after hashes."""
        manifest: list[dict[str, Any]] = []
        for relative in changed_paths:
            rel = Path(relative)
            if rel.is_absolute() or any(part in _FORBIDDEN_PARTS for part in rel.parts):
                raise OmpWorktreeError("patch path is outside policy")
            source = self.source_root / rel
            worktree = self.worktree / rel
            if not worktree.is_file():
                raise OmpWorktreeError("patch path absent from the worktree")
            if not source.is_file():
                raise OmpWorktreeError("patch path absent from the source tree")
            before = source.read_bytes()
            after = worktree.read_bytes()
            if before == after:
                continue
            manifest.append({
                "path": str(rel),
                "before_hash": _sha256(before),
                "after_hash": _sha256(after),
            })
        if not manifest:
            raise OmpWorktreeError("no changed paths in the manifest")
        patch_text = _build_patch(manifest, self.source_root, self.worktree)
        return {
            "schema_version": "1.0",
            "source_hash": self.source_hash,
            "final_tree_hash": self.final_tree_hash(),
            "changed_paths": [item["path"] for item in manifest],
            "manifest": manifest,
            "patch": patch_text,
            "patch_hash": _sha256(patch_text.encode("utf-8")),
        }

    def cleanup(self) -> dict[str, Any]:
        self._temporary.cleanup()
        return {"cleanup_receipt": _sha256(b"cleaned")}


def _build_patch(manifest: list[dict[str, Any]], source_root: Path, worktree: Path) -> str:
    """Build a unified diff for the changed files."""
    sections: list[str] = []
    for item in manifest:
        rel = Path(item["path"])
        before = (source_root / rel).read_bytes().decode("utf-8", errors="replace")
        after = (worktree / rel).read_bytes().decode("utf-8", errors="replace")
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        sections.append(f"--- a/{rel}\n+++ b/{rel}\n@@ -1,{len(before_lines)} +1,{len(after_lines)} @@\n" + "".join(after_lines))
    return "\n".join(sections)
