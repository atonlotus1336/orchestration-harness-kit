#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"
ROOT_HERMES_HOME="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
ACTIVE_PROFILE="$(cat "$ROOT_HERMES_HOME/active_profile" 2>/dev/null || true)"
ACTIVE_PROFILE="${ACTIVE_PROFILE:-home}"
HERMES_BIN="${HERMES_BIN:-$ROOT_HERMES_HOME/hermes-agent/venv/bin/hermes}"
OPTIMIZER_REPO="${HERMES_OPTIMIZER_REPO:-__WSL_WORKSPACE__/hermes-optimizer}"
__PRIVATE_TOKEN_02___HERMES_HOME="${__PRIVATE_TOKEN_02___HERMES_HOME:-$ROOT_HERMES_HOME/profiles/$ACTIVE_PROFILE}"
CATEGORY="autonomous-ai-agents"
PROFILE_TARGET_DIR="$ROOT_HERMES_HOME/profiles/$ACTIVE_PROFILE/skills/$CATEGORY/hermes-optimizer"
__PRIVATE_TOKEN_02___TARGET_DIR="$__PRIVATE_TOKEN_02___HERMES_HOME/skills/$CATEGORY/hermes-optimizer"
__PRIVATE_TOKEN_02___INSTALL_MODE=""
__PRIVATE_TOKEN_02___STATUS=""
__PRIVATE_TOKEN_02___META_FILE="$__PRIVATE_TOKEN_02___TARGET_DIR/.hermes-install-meta"

if [ ! -d "$OPTIMIZER_REPO" ]; then
  echo "Hermes Optimizer repo missing: $OPTIMIZER_REPO" >&2
  exit 1
fi

chmod +x "$OPTIMIZER_REPO"/scripts/*.sh >/dev/null 2>&1 || true
mkdir -p "$OPTIMIZER_REPO/output/snapshots"

tree_hash() {
  local target_dir="$1"
  python3 - "$target_dir" <<'PY'
import hashlib
import os
import sys

root = os.path.abspath(sys.argv[1])
exclude_dirs = {'.git', 'output/snapshots'}
exclude_files = {'.hermes-install-meta'}
h = hashlib.sha256()

for current_root, dirs, files in os.walk(root):
    rel_root = os.path.relpath(current_root, root)
    normalized_root = '' if rel_root == '.' else rel_root.replace('\\', '/')
    dirs[:] = [
        d for d in sorted(dirs)
        if f"{normalized_root}/{d}".strip('/') not in exclude_dirs and d != '.git'
    ]
    for name in sorted(files):
        rel_path = f"{normalized_root}/{name}".strip('/').replace('\\', '/')
        if rel_path in exclude_dirs or name in exclude_files:
            continue
        file_path = os.path.join(current_root, name)
        h.update(rel_path.encode('utf-8', errors='surrogateescape'))
        h.update(b'\0')
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
print(h.hexdigest())
PY
}

write_meta() {
  local status="$1"
  local repo_hash="$2"
  mkdir -p "$__PRIVATE_TOKEN_02___TARGET_DIR"
  cat > "$__PRIVATE_TOKEN_02___META_FILE" <<EOF
source_repo=$OPTIMIZER_REPO
mode=$__PRIVATE_TOKEN_02___INSTALL_MODE
status=$status
profile=$ACTIVE_PROFILE
repo_hash=$repo_hash
updated_at=$(date -Is)
EOF
}

bash "$OPTIMIZER_REPO/scripts/ensure-installed.sh" --profile "$ACTIVE_PROFILE" --link --verify >/dev/null

if [ ! -e "$PROFILE_TARGET_DIR/SKILL.md" ]; then
  echo "Hermes Optimizer profile install missing at $PROFILE_TARGET_DIR" >&2
  exit 1
fi

mkdir -p "$(dirname "$__PRIVATE_TOKEN_02___TARGET_DIR")"

ensure___PRIVATE_TOKEN_02___copy() {
  local tmp_dir
  tmp_dir="$(mktemp -d "$(dirname "$__PRIVATE_TOKEN_02___TARGET_DIR")/.hermes-optimizer.tmp.XXXXXX")"
  rsync -a --delete --exclude '.git' --exclude 'output/snapshots' "$OPTIMIZER_REPO/" "$tmp_dir/"
  rm -rf "$__PRIVATE_TOKEN_02___TARGET_DIR"
  mv "$tmp_dir" "$__PRIVATE_TOKEN_02___TARGET_DIR"
  __PRIVATE_TOKEN_02___INSTALL_MODE="synced-copy"
}

if [ -L "$__PRIVATE_TOKEN_02___TARGET_DIR" ]; then
  current_target="$(readlink -f "$__PRIVATE_TOKEN_02___TARGET_DIR" 2>/dev/null || true)"
  if [ "$current_target" = "$OPTIMIZER_REPO" ]; then
    __PRIVATE_TOKEN_02___INSTALL_MODE="symlink"
  else
    rm -rf "$__PRIVATE_TOKEN_02___TARGET_DIR"
  fi
fi

if [ -z "$__PRIVATE_TOKEN_02___INSTALL_MODE" ] && [ ! -e "$__PRIVATE_TOKEN_02___TARGET_DIR" ]; then
  if ln -s "$OPTIMIZER_REPO" "$__PRIVATE_TOKEN_02___TARGET_DIR" 2>/dev/null; then
    __PRIVATE_TOKEN_02___INSTALL_MODE="symlink"
  else
    WIN_TARGET="$(wslpath -w "$__PRIVATE_TOKEN_02___TARGET_DIR")"
    WIN_REPO="$(wslpath -w "$OPTIMIZER_REPO")"
    if /mnt/c/Windows/System32/cmd.exe /c "mklink /D \"$WIN_TARGET\" \"$WIN_REPO\"" >/dev/null 2>&1; then
      __PRIVATE_TOKEN_02___INSTALL_MODE="windows-symlink"
    else
      ensure___PRIVATE_TOKEN_02___copy
    fi
  fi
fi

if [ -z "$__PRIVATE_TOKEN_02___INSTALL_MODE" ]; then
  if [ -d "$__PRIVATE_TOKEN_02___TARGET_DIR" ]; then
    ensure___PRIVATE_TOKEN_02___copy
  else
    echo "Hermes Optimizer WorkspaceKit install missing at $__PRIVATE_TOKEN_02___TARGET_DIR" >&2
    exit 1
  fi
fi

if [ ! -e "$__PRIVATE_TOKEN_02___TARGET_DIR/SKILL.md" ]; then
  echo "Hermes Optimizer WorkspaceKit install missing SKILL.md at $__PRIVATE_TOKEN_02___TARGET_DIR" >&2
  exit 1
fi

repo_hash="$(tree_hash "$OPTIMIZER_REPO")"
if [ "$__PRIVATE_TOKEN_02___INSTALL_MODE" = "symlink" ] || [ "$__PRIVATE_TOKEN_02___INSTALL_MODE" = "windows-symlink" ]; then
  __PRIVATE_TOKEN_02___hash="$repo_hash"
  __PRIVATE_TOKEN_02___STATUS="linked"
  write_meta "$__PRIVATE_TOKEN_02___STATUS" "$repo_hash"
else
  __PRIVATE_TOKEN_02___hash="$(tree_hash "$__PRIVATE_TOKEN_02___TARGET_DIR")"
  if [ "$__PRIVATE_TOKEN_02___hash" != "$repo_hash" ]; then
    echo "Hermes Optimizer WorkspaceKit synced copy drifted from canonical repo; re-syncing $__PRIVATE_TOKEN_02___TARGET_DIR" >&2
    ensure___PRIVATE_TOKEN_02___copy
    __PRIVATE_TOKEN_02___hash="$(tree_hash "$__PRIVATE_TOKEN_02___TARGET_DIR")"
  fi
  if [ "$__PRIVATE_TOKEN_02___hash" != "$repo_hash" ]; then
    echo "Hermes Optimizer WorkspaceKit install hash mismatch after sync: repo=$repo_hash target=$__PRIVATE_TOKEN_02___hash" >&2
    exit 1
  fi
  __PRIVATE_TOKEN_02___STATUS="in-sync"
  write_meta "$__PRIVATE_TOKEN_02___STATUS" "$repo_hash"
fi

if [ -x "$HERMES_BIN" ]; then
  profile_skills_output="$(HERMES_HOME="$ROOT_HERMES_HOME/profiles/$ACTIVE_PROFILE" "$HERMES_BIN" skills list 2>/dev/null || true)"
  if ! printf '%s' "$profile_skills_output" | grep -q 'hermes-optimizer'; then
    echo "Hermes Optimizer installed at $PROFILE_TARGET_DIR but not visible in fresh Hermes skills list for profile $ACTIVE_PROFILE" >&2
    exit 1
  fi

  __PRIVATE_TOKEN_02___skills_output="$(HERMES_HOME="$__PRIVATE_TOKEN_02___HERMES_HOME" "$HERMES_BIN" skills list 2>/dev/null || true)"
  if ! printf '%s' "$__PRIVATE_TOKEN_02___skills_output" | grep -q 'hermes-optimizer'; then
    echo "Hermes Optimizer present at $__PRIVATE_TOKEN_02___TARGET_DIR but not visible in fresh Hermes skills list for $__PRIVATE_TOKEN_02___HERMES_HOME" >&2
    exit 1
  fi
fi

echo "Hermes Optimizer ready: profile=$PROFILE_TARGET_DIR __PRIVATE_TOKEN_02__=$__PRIVATE_TOKEN_02___TARGET_DIR mode=$__PRIVATE_TOKEN_02___INSTALL_MODE status=$__PRIVATE_TOKEN_02___STATUS hash=$repo_hash"
