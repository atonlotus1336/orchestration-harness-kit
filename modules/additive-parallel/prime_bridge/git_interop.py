"""Machine-neutral Git argv construction for Orca-owned Windows worktrees.

Prime normally uses the host-native Git.  A Windows Orca runtime writes linked
worktree metadata with a Windows ``gitdir`` path, which WSL Git cannot resolve.
This module selects Windows Git only for that exact metadata shape and converts
paths at the process boundary; all policy and evidence checks remain unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil


_WINDOWS_GITDIR = re.compile(r"^gitdir:\s*[A-Za-z]:[\\\\/]", re.IGNORECASE)
_WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\\\/](.*)$")


def uses_windows_git(root: Path) -> bool:
    if os.name == "nt":
        return False
    marker = Path(root) / ".git"
    if not marker.is_file():
        return False
    try:
        first = marker.read_text(encoding="utf-8", errors="strict").splitlines()[0]
    except (OSError, UnicodeError, IndexError):
        return False
    return _WINDOWS_GITDIR.match(first) is not None


def to_windows_path(value: str | Path) -> str:
    rendered = str(value)
    if os.name == "nt":
        return rendered
    match = re.fullmatch(r"/mnt/([A-Za-z])(?:/(.*))?", rendered)
    if match is None:
        return rendered
    tail = (match.group(2) or "").replace("/", "\\")
    return f"{match.group(1).upper()}:\\{tail}"


def to_local_path(value: str | Path) -> Path:
    rendered = str(value).strip()
    if os.name != "nt":
        match = _WINDOWS_PATH.match(rendered)
        if match is not None:
            tail = match.group(2).replace("\\", "/")
            return Path(f"/mnt/{match.group(1).lower()}/{tail}")
    return Path(rendered)


def git_argv(root: Path, *arguments: str, optional_locks: bool = True) -> list[str]:
    root = Path(root)
    windows = uses_windows_git(root)
    executable = "git"
    rendered_root = str(root)
    if windows:
        executable = shutil.which("git.exe") or ""
        if not executable:
            raise FileNotFoundError("Windows Git is unavailable for Orca worktree")
        rendered_root = to_windows_path(root)
    command = [executable]
    if not optional_locks:
        command.append("--no-optional-locks")
    command.extend(["-C", rendered_root])
    command.extend(
        to_windows_path(item) if windows and str(item).startswith("/mnt/") else str(item)
        for item in arguments
    )
    return command


__all__ = ["git_argv", "to_local_path", "to_windows_path", "uses_windows_git"]
