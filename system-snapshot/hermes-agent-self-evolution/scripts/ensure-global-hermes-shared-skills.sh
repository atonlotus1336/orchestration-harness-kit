#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"

SHARED_SKILLS_DIR="${HERMES_SHARED_SKILLS_DIR:-__WSL_WORKSPACE__/skills-active}"
WINDOWS_SHARED_SKILLS_DIR="${HERMES_WINDOWS_SHARED_SKILLS_DIR:-__WINDOWS_WORKSPACE__/skills-active}"
WSL_ROOT="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
WIN_ROOT="${HERMES_WINDOWS_ROOT:-/mnt/c/Users/__WINDOWS_USER__/.hermes}"

python3 - "$SHARED_SKILLS_DIR" "$WINDOWS_SHARED_SKILLS_DIR" "$WSL_ROOT" "$WIN_ROOT" <<'PY'
from pathlib import Path
import json
import sys

shared_root = Path(sys.argv[1])
windows_shared_entry = sys.argv[2]
roots = [
    ("wsl", Path(sys.argv[3]), [str(shared_root)]),
    ("windows", Path(sys.argv[4]), [windows_shared_entry]),
]
changed: list[str] = []
checked: list[str] = []

if not shared_root.is_dir():
    raise SystemExit(f"Shared skills directory is missing: {shared_root}")


def line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def find_block_end(lines: list[str], start: int, indent: int) -> int:
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and line_indent(line) <= indent:
            break
        end += 1
    return end


def find_external_dirs_block_end(lines: list[str], start: int) -> int:
    end = start + 1
    while end < len(lines):
        line = lines[end]
        stripped = line.strip()
        indent = line_indent(line)
        if not stripped:
            end += 1
            continue
        if indent > 2 or (indent == 2 and stripped.startswith("- ")):
            end += 1
            continue
        break
    return end


def ensure_shared_external_dir(text: str, shared_entries: list[str]) -> tuple[str, bool]:
    lines = text.splitlines()
    external_lines = [f"  - {entry}" for entry in shared_entries]
    if not lines:
        return "skills:\n  external_dirs:\n" + "\n".join(external_lines) + "\n", True

    skills_idx = None
    for i, line in enumerate(lines):
        if line == "skills:":
            skills_idx = i
            break

    if skills_idx is None:
        suffix = "" if text.endswith("\n") else "\n"
        return f"{text}{suffix}skills:\n  external_dirs:\n" + "\n".join(external_lines) + "\n", True

    skills_end = find_block_end(lines, skills_idx, 0)
    cleaned: list[str] = []
    removed_any = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if skills_idx < i < skills_end and stripped.startswith("external_dirs:") and line_indent(lines[i]) == 2:
            block_end = i + 1 if stripped != "external_dirs:" else find_external_dirs_block_end(lines, i)
            removed_any = True
            i = block_end
            continue
        cleaned.append(lines[i])
        i += 1

    old_text = "\n".join(lines) + "\n"
    lines = cleaned
    skills_idx = next(i for i, line in enumerate(lines) if line == "skills:")
    lines[skills_idx + 1:skills_idx + 1] = ["  external_dirs:", *external_lines]
    new_text = "\n".join(lines) + "\n"
    return new_text, removed_any or new_text != old_text


for _root_name, root, root_shared_entries in roots:
    profiles = root / "profiles"
    if not profiles.is_dir():
        continue
    for profile in sorted(p for p in profiles.iterdir() if p.is_dir()):
        cfg = profile / "config.yaml"
        if not cfg.exists():
            continue
        checked.append(str(cfg))
        original = cfg.read_text(encoding="utf-8-sig", errors="ignore")
        updated, did_change = ensure_shared_external_dir(original, root_shared_entries)
        if did_change:
            cfg.write_text(updated, encoding="utf-8")
            changed.append(str(cfg))
        legacy_link = profile / "skills" / "shared-g-hermes"
        if legacy_link.exists() or legacy_link.is_symlink():
            if legacy_link.is_dir() and not legacy_link.is_symlink():
                continue
            legacy_link.unlink(missing_ok=True)

print(json.dumps({
    "status": "ok",
    "shared_skills_dir": str(shared_root),
    "shared_external_dirs": {
        root_name: entries for root_name, _root, entries in roots
    },
    "checked_count": len(checked),
    "changed_count": len(changed),
    "changed": changed,
}, indent=2))
PY
