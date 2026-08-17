#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"

SHARED_SKILLS_DIR="${HERMES_SHARED_SKILLS_DIR:-__WSL_WORKSPACE__/skills-active}"
WINDOWS_SHARED_SKILLS_DIR="${HERMES_WINDOWS_SHARED_SKILLS_DIR:-__WINDOWS_WORKSPACE__/skills-active}"
WSL_ROOT="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
WIN_ROOT="${HERMES_WINDOWS_ROOT:-/mnt/c/Users/__WINDOWS_USER__/.hermes}"
HERMES_BIN="${HERMES_BIN:-$WSL_ROOT/hermes-agent/venv/bin/hermes}"

python3 - "$SHARED_SKILLS_DIR" "$WINDOWS_SHARED_SKILLS_DIR" "$WSL_ROOT" "$WIN_ROOT" <<'PY'
from pathlib import Path
import json
import sys

shared_root = Path(sys.argv[1])
windows_shared_entry = sys.argv[2]
roots = {
    "wsl": (Path(sys.argv[3]), [str(shared_root)]),
    "windows": (Path(sys.argv[4]), [windows_shared_entry]),
}
missing = []
profiles = {}

for root_name, (root, shared_entries) in roots.items():
    profile_root = root / "profiles"
    root_profiles = {}
    if profile_root.is_dir():
        for profile in sorted(p for p in profile_root.iterdir() if p.is_dir()):
            cfg = profile / "config.yaml"
            if not cfg.exists():
                continue
            text = cfg.read_text(encoding="utf-8", errors="ignore")
            lines = text.splitlines()
            external_dir_entries = []
            in_external_dirs = False
            for line in lines:
                stripped = line.strip()
                indent = len(line) - len(line.lstrip(" "))
                if stripped.startswith("external_dirs:") and indent == 2:
                    if stripped != "external_dirs:":
                        external_dir_entries.append("__inline_external_dirs__")
                        in_external_dirs = False
                        continue
                    in_external_dirs = True
                    continue
                if in_external_dirs:
                    if stripped.startswith("- ") and indent == 2:
                        external_dir_entries.append(stripped[2:].strip())
                        continue
                    if stripped and indent <= 2:
                        in_external_dirs = False
            has_shared = all(entry in external_dir_entries for entry in shared_entries)
            has_duplicates = len(external_dir_entries) != len(set(external_dir_entries))
            root_profiles[profile.name] = has_shared and not has_duplicates
            if not has_shared:
                missing.append(f"{root_name}:{profile.name}")
            if has_duplicates:
                missing.append(f"{root_name}:{profile.name}:duplicate_external_dirs")
            if "__inline_external_dirs__" in external_dir_entries:
                missing.append(f"{root_name}:{profile.name}:inline_external_dirs")
    profiles[root_name] = root_profiles

status = "ok" if shared_root.is_dir() and not missing else "error"
print(json.dumps({
    "status": status,
    "shared_skills_exists": shared_root.is_dir(),
    "shared_external_dirs": {name: entries for name, (_root, entries) in roots.items()},
    "missing_shared_path": missing,
    "profiles": profiles,
}, indent=2))
raise SystemExit(0 if status == "ok" else 1)
PY

if [ ! -x "$HERMES_BIN" ]; then
  echo "Hermes binary not executable: $HERMES_BIN" >&2
  exit 1
fi

for profile in telegram gpt deepseekv4 gemmalocal qwen3coder; do
  home="$WSL_ROOT/profiles/$profile"
  [ -f "$home/config.yaml" ] || continue
  echo "--- skills:$profile"
  HERMES_HOME="$home" "$HERMES_BIN" skills list \
    | grep -E 'telegram-switch-models|brainstorm-council|hermes-agent|professor-__PRIVATE_TOKEN_05__|__PRIVATE_TOKEN_02__-project-locator' || {
      echo "Expected shared skills missing for $profile" >&2
      exit 1
    }
done
