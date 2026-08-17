"""Hermes-approved project instruction bundle for OMP (sanitized, hash-bound)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from prime_bridge.omp_policy import OmpPolicyError, build_instruction_bundle


def project_source_hash(project_root: Path) -> str:
    """Hash the authoritative source tree deterministically (fail closed)."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise OmpPolicyError("project root is not a directory")
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


def load_approved_rules(project_root: Path, rules_path: Path | None = None) -> dict[str, Any]:
    """Load Hermes-approved project rules (AGENTS.md/CLAUDE.md) as a bounded object."""
    root = Path(project_root).resolve()
    candidates = [rules_path] if rules_path is not None else [
        root / "AGENTS.md", root / "CLAUDE.md", root / ".cursorrules",
    ]
    rules: dict[str, Any] = {"files": [], "content": {}}
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(candidate).resolve()
        if not path.is_file() or not path.is_relative_to(root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        rules["files"].append(str(path.relative_to(root)))
        rules["content"][str(path.relative_to(root))] = text[:64_000]
    return rules


def build_project_instruction_bundle(
    project_root: Path, *, rules_path: Path | None = None,
) -> dict[str, Any]:
    """Build the sanitized, hash-bound instruction bundle for one OMP job."""
    root = Path(project_root).resolve()
    source_hash = project_source_hash(root)
    approved = load_approved_rules(root, rules_path)
    return build_instruction_bundle(
        project_root=root, approved_rules=approved, source_hash=source_hash,
    )
