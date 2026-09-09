"""Canonical deterministic Git observation; no evaluator imports."""
from __future__ import annotations
import hashlib, json, os, stat, subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

MAX_FILE_BYTES = 16 * 1024 * 1024


MAX_PATHS = 20000


class EvidenceError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def refuse_git_filters(root: Path) -> None:
    """Inspect configuration before Git can execute any content filter."""
    try:
        result = subprocess.run(["git", "-C", str(root), "config", "--get-regexp",
                                 r"^filter\..*\.(clean|smudge|process)$"],
                                capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("checkout drivers unknown; use broker isolation") from exc
    if result.returncode != 1:
        raise EvidenceError("checkout drivers present or unknown; use broker isolation")


def _git(root: Path, *args: str) -> bytes:
    refuse_git_filters(root)
    result = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
                            cwd=root, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise EvidenceError("Git observation failed: " + args[0])
    if len(result.stdout) > 16 * 1024 * 1024:
        raise EvidenceError("Git observation exceeds evidence budget")
    return result.stdout


def _path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if not parts or name in {".", ""} or "\\" in name or ":" in name or name.startswith("/") or ".." in parts:
        raise EvidenceError("nonportable or escaping evidence path")
    if any(p.casefold() in {".git", ".env", "secrets", "credentials"} or p.casefold().startswith(".env.") for p in parts):
        raise EvidenceError("sensitive evidence path refused")
    target = root.joinpath(*parts)
    for candidate in (target, *target.parents):
        if candidate == root:
            break
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise EvidenceError("symlink/junction evidence paths refused")
    if not target.resolve().is_relative_to(root):
        raise EvidenceError("evidence path escaped root")
    return target


def _file_hash(root: Path, name: str) -> str:
    target = _path(root, name)
    if not target.exists():
        return "ABSENT"
    if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
        raise EvidenceError("unsupported or oversized evidence file")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _opaque_metadata(root: Path, name: str) -> str:
    """Observe unrelated sensitive/symlink entries without opening their bytes."""
    target = root
    for part in PurePosixPath(name).parts:
        if part in {"", ".", ".."} or ":" in part or "\\" in part or part.startswith("/"):
            raise EvidenceError("cannot safely observe nonportable Git path")
        target = target / part
        try:
            info = target.lstat()
        except FileNotFoundError:
            return "ABSENT"
        if stat.S_ISLNK(info.st_mode) or (hasattr(target, "is_junction") and target.is_junction()):
            break
    return "METADATA_ONLY:" + digest([info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns])


def snapshot(root: Path, declared: tuple[str, ...]) -> dict[str, Any]:
    """Index blob identities + dirty/untracked byte hashes; no whole-vault crawl.

    Ignored files are outside observation, except explicitly declared paths.
    Generated evidence is excluded to prevent receipts observing themselves.
    This detects modifications to already-dirty files, not just status changes.
    """
    root = root.resolve(strict=True)
    top = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if root != top:
        raise EvidenceError("workspace must be the exact Git repository root")
    head = _git(root, "rev-parse", "--verify", "HEAD").decode().strip()
    index: dict[str, str] = {}
    for row in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not row:
            continue
        meta, raw_path = row.split(b"\t", 1)
        mode, blob, stage = meta.decode().split()
        if stage != "0":
            raise EvidenceError("unmerged index cannot produce evidence")
        name = os.fsdecode(raw_path)
        if not name.startswith(".planning/review-evidence/"):
            index[name] = mode + ":" + blob
    names = set(declared)
    for args in (("diff", "--name-only", "--no-ext-diff", "--no-textconv", "--no-renames", "HEAD", "-z", "--"),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        names.update(os.fsdecode(p) for p in _git(root, *args).split(b"\0") if p)
    names = {p for p in names if not p.startswith(".planning/review-evidence/")}
    if len(names) + len(index) > MAX_PATHS:
        raise EvidenceError("path budget exceeded")
    contents = {}
    worktree_modes = {}
    filemode = _git(root, "config", "--bool", "core.filemode").strip() == b"true"
    unobserved = []
    remaining = 64 * 1024 * 1024
    for name in sorted(names, key=lambda p: (p not in declared, p)):
        try:
            target = _path(root, name)
            size = target.stat().st_size if target.exists() else 0
            if size > remaining:
                raise EvidenceError("content observation budget exceeded")
            contents[name] = _file_hash(root, name)
            mode = ("100755" if target.stat().st_mode & 0o100 else "100644") if contents[name] != "ABSENT" else None
            if mode is not None and not filemode:
                mode = index.get(name, "100644").split(":", 1)[0]
            worktree_modes[name] = mode
            remaining -= size
        except EvidenceError:
            if name in declared:
                raise
            contents[name] = _opaque_metadata(root, name)
            unobserved.append(name)
    state = {"head": head, "index": index, "contents": contents,
             "worktree_modes": worktree_modes, "filemode": filemode,
             "unobserved_paths": sorted(unobserved)}
    return {**state, "identity": digest(state)}


def changed_paths(root: Path, before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    changed: set[str] = set()
    if before["head"] != after["head"]:
        changed.update(os.fsdecode(p) for p in _git(root, "diff", "--name-only", "--no-ext-diff", "--no-textconv", "--no-renames", before["head"], after["head"], "-z", "--").split(b"\0") if p)
    for field in ("index", "contents", "worktree_modes"):
        a, b = before.get(field, {}), after.get(field, {})
        changed.update(p for p in set(a) | set(b) if a.get(p) != b.get(p))
    return sorted(changed)
