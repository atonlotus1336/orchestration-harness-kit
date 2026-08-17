#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"

G_ROOT="__WSL_WORKSPACE__/hermes-agent-self-evolution"
PROFILE_SOURCE="$G_ROOT/profiles/telegram"
PLUGIN_SOURCE="__WSL_WORKSPACE__/hermes-custom-plugins/image_gen/comfyui-bridge"
__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE="__WSL_WORKSPACE__/hermes-custom-plugins/__PRIVATE_TOKEN_02__-parity"
SHARED_SKILLS_SOURCE="__WSL_WORKSPACE__/skills-active"
WSL_ROOT="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
WIN_ROOT="${HERMES_WINDOWS_ROOT:-/mnt/c/Users/${WINDOWS_USER:-__WINDOWS_USER__}/.hermes}"
WSL_PROFILE="$WSL_ROOT/profiles/telegram"
WIN_PROFILE="$WIN_ROOT/profiles/telegram"
WSL_PLUGIN_LINK="$WSL_PROFILE/plugins/image_gen/comfyui-bridge"
WSL___PRIVATE_TOKEN_02___PARITY_PLUGIN_LINK="$WSL_PROFILE/plugins/__PRIVATE_TOKEN_02__-parity"
WIN___PRIVATE_TOKEN_02___PARITY_PLUGIN="$WIN_PROFILE/plugins/__PRIVATE_TOKEN_02__-parity"

copy_profile_tree() {
  local src="$1"
  local dst="$2"
  mkdir -p "$dst"
  cp "$src/config.yaml" "$dst/config.yaml"
  cp "$src/.env" "$dst/.env"
  if [ -f "$src/README.md" ]; then
    cp "$src/README.md" "$dst/README.md"
  fi
}

merge_runtime_keys() {
  local src="$1"
  local dst="$2"
  [ -f "$src" ] || return 0
  [ -f "$dst" ] || return 0
  python3 - "$src" "$dst" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
allowed = {
    "API_SERVER_ENABLED",
    "API_SERVER_HOST",
    "API_SERVER_PORT",
    "API_SERVER_KEY",
    "API_SERVER_MODEL_NAME",
}

values = {}
for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
    if "=" not in line or line.lstrip().startswith("#"):
        continue
    key, value = line.split("=", 1)
    if key in allowed and value:
        values[key] = value
if not values:
    raise SystemExit(0)

lines = dst.read_text(encoding="utf-8", errors="ignore").splitlines()
out = []
seen = set()
changed = False
for line in lines:
    if "=" not in line or line.lstrip().startswith("#"):
        out.append(line)
        continue
    key, _value = line.split("=", 1)
    if key in values:
        next_line = f"{key}={values[key]}"
        out.append(next_line)
        seen.add(key)
        changed = changed or line != next_line
    else:
        out.append(line)
for key, value in values.items():
    if key not in seen:
        out.append(f"{key}={value}")
        changed = True
if changed:
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

strip_telegram_keys() {
  local env_file="$1"
  [ -f "$env_file" ] || return 0
  python3 - "$env_file" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
out = []
changed = False
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        key = line.split('=', 1)[0].strip()
        if key.startswith('TELEGRAM_'):
            changed = True
            continue
    out.append(line)
if changed:
    path.write_text('\n'.join(out) + ('\n' if out else ''), encoding='utf-8')
PY
}

force_telegram_api_runtime() {
  local env_file="$1"
  [ -f "$env_file" ] || return 0
  python3 - "$env_file" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    "API_SERVER_ENABLED": "true",
    "API_SERVER_HOST": "127.0.0.1",
    "API_SERVER_PORT": "8643",
}
lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
out = []
seen = set()
changed = False
for line in lines:
    if "=" not in line or line.lstrip().startswith("#"):
        out.append(line)
        continue
    key, _value = line.split("=", 1)
    if key in updates:
        next_line = f"{key}={updates[key]}"
        out.append(next_line)
        seen.add(key)
        changed = changed or line != next_line
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
        changed = True
if changed:
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

main() {
  [ -f "$PROFILE_SOURCE/config.yaml" ] || { echo "Missing $PROFILE_SOURCE/config.yaml" >&2; exit 1; }
  [ -f "$PROFILE_SOURCE/.env" ] || { echo "Missing $PROFILE_SOURCE/.env" >&2; exit 1; }
  [ -f "$PLUGIN_SOURCE/__init__.py" ] || { echo "Missing $PLUGIN_SOURCE/__init__.py" >&2; exit 1; }
  [ -f "$PLUGIN_SOURCE/plugin.yaml" ] || { echo "Missing $PLUGIN_SOURCE/plugin.yaml" >&2; exit 1; }
  # ponytail: plugin source deleted post-2026-07-08; warn instead of hard-fail so gateway can start
  [ -f "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE/__init__.py" ] || echo "WARNING: missing $__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE/__init__.py (__PRIVATE_TOKEN_02__-parity sync will be skipped)" >&2
  [ -f "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE/plugin.yaml" ] || echo "WARNING: missing $__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE/plugin.yaml (__PRIVATE_TOKEN_02__-parity sync will be skipped)" >&2
  [ -d "$SHARED_SKILLS_SOURCE" ] || { echo "Missing $SHARED_SKILLS_SOURCE" >&2; exit 1; }

  if [ "${HERMES_SKIP_OLLAMA_ALIAS_ENSURE:-}" != "1" ] && [ -x "$G_ROOT/scripts/ensure-telegram-ollama-model.sh" ]; then
    "$G_ROOT/scripts/ensure-telegram-ollama-model.sh" --warn-only
  fi

  mkdir -p "$WSL_ROOT/profiles" "$WIN_ROOT/profiles" "$(dirname "$WSL_PLUGIN_LINK")" "$(dirname "$WSL___PRIVATE_TOKEN_02___PARITY_PLUGIN_LINK")"
  copy_profile_tree "$PROFILE_SOURCE" "$WSL_PROFILE"
  copy_profile_tree "$PROFILE_SOURCE" "$WIN_PROFILE"
  merge_runtime_keys "$WSL_ROOT/.env" "$WSL_PROFILE/.env"
  merge_runtime_keys "$WIN_ROOT/.env" "$WIN_PROFILE/.env"
  force_telegram_api_runtime "$WSL_PROFILE/.env"
  force_telegram_api_runtime "$WIN_PROFILE/.env"

  rm -rf "$WSL_ROOT/plugins/image_gen/comfyui-bridge"
  ln -sfn "$PLUGIN_SOURCE" "$WSL_PLUGIN_LINK"
  rm -rf "$WSL_PROFILE/plugins/__PRIVATE_TOKEN_02__-parity"
  mkdir -p "$WIN_PROFILE/plugins"
  if [ ! -d "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE" ]; then
    # ponytail: source deleted from G: after 2026-07-08 with no known backup —
    # skip sync + drop stale links so gateway startup isn't blocked
    echo "WARNING: __PRIVATE_TOKEN_02__-parity plugin source missing at $__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE; skipping its sync" >&2
    rm -f "$WIN___PRIVATE_TOKEN_02___PARITY_PLUGIN" 2>/dev/null || true
  else
  ln -sfn "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE" "$WSL___PRIVATE_TOKEN_02___PARITY_PLUGIN_LINK"
  if command -v powershell.exe >/dev/null 2>&1; then
    src_win="$(wslpath -w "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE")"
    dst_win="$(wslpath -w "$WIN___PRIVATE_TOKEN_02___PARITY_PLUGIN")"
    WSLENV="WIN___PRIVATE_TOKEN_02___SRC:WIN___PRIVATE_TOKEN_02___DST:${WSLENV:-}" WIN___PRIVATE_TOKEN_02___SRC="$src_win" WIN___PRIVATE_TOKEN_02___DST="$dst_win" powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
      $ErrorActionPreference = "Stop"
      $src = $env:WIN___PRIVATE_TOKEN_02___SRC
      $dst = $env:WIN___PRIVATE_TOKEN_02___DST
      if (Test-Path -LiteralPath $dst) {
        $item = Get-Item -LiteralPath $dst -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
          $item.Delete()
        } else {
          Remove-Item -LiteralPath $dst -Recurse -Force
        }
      }
      try {
        New-Item -ItemType Junction -Path $dst -Target $src -ErrorAction Stop | Out-Null
      } catch {
        Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
      }
    ' >/dev/null
  else
    [ ! -e "$WIN___PRIVATE_TOKEN_02___PARITY_PLUGIN" ] || { echo "Cannot safely replace $WIN___PRIVATE_TOKEN_02___PARITY_PLUGIN without powershell.exe" >&2; exit 1; }
    ln -s "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE" "$WIN___PRIVATE_TOKEN_02___PARITY_PLUGIN"
  fi
  fi
  rm -rf "$WSL_PROFILE/skills/shared-g-hermes" "$WIN_PROFILE/skills/shared-g-hermes"

  strip_telegram_keys "$WSL_ROOT/.env"
  strip_telegram_keys "$WIN_ROOT/.env"

  while IFS= read -r env_file; do
    case "$env_file" in
      "$WSL_PROFILE/.env"|"$WIN_PROFILE/.env")
        ;;
      *)
        strip_telegram_keys "$env_file"
        ;;
    esac
  done < <(find "$WSL_ROOT/profiles" "$WIN_ROOT/profiles" -mindepth 2 -maxdepth 2 -name .env 2>/dev/null | sort)

  if [ "${HERMES_ACTIVATE_TELEGRAM_PROFILE:-}" = "1" ]; then
    printf 'telegram\n' > "$WSL_ROOT/active_profile"
    printf 'telegram\n' > "$WIN_ROOT/active_profile"
  else
    [ -s "$WSL_ROOT/active_profile" ] || printf 'home\n' > "$WSL_ROOT/active_profile"
    [ -s "$WIN_ROOT/active_profile" ] || printf 'home\n' > "$WIN_ROOT/active_profile"
  fi

  python3 - <<'PY'
from pathlib import Path
import json
import os
wsl_root = Path.home() / '.hermes'
summary = {
    'status': 'ok',
    'g_profile': '__WSL_WORKSPACE__/hermes-agent-self-evolution/profiles/telegram',
    'wsl_profile': str(wsl_root / 'profiles' / 'telegram'),
    'windows_profile': str(Path(os.environ.get('HERMES_WINDOWS_ROOT', '/mnt/c/Users/__WINDOWS_USER__/.hermes')) / 'profiles' / 'telegram'),
    'wsl_plugin_link': str(wsl_root / 'profiles' / 'telegram' / 'plugins' / 'image_gen' / 'comfyui-bridge'),
    'wsl___PRIVATE_TOKEN_02___parity_plugin_link': str(wsl_root / 'profiles' / 'telegram' / 'plugins' / '__PRIVATE_TOKEN_02__-parity'),
    'windows___PRIVATE_TOKEN_02___parity_plugin_copy': str(Path(os.environ.get('HERMES_WINDOWS_ROOT', '/mnt/c/Users/__WINDOWS_USER__/.hermes')) / 'profiles' / 'telegram' / 'plugins' / '__PRIVATE_TOKEN_02__-parity'),
    'shared_skills_source': '__WSL_WORKSPACE__/skills-active',
    'telegram_api_port': '8643',
    'active_profile_wsl': (wsl_root / 'active_profile').read_text(encoding='utf-8', errors='ignore').strip(),
    'active_profile_windows': (Path(os.environ.get('HERMES_WINDOWS_ROOT', '/mnt/c/Users/__WINDOWS_USER__/.hermes')) / 'active_profile').read_text(encoding='utf-8', errors='ignore').strip(),
}
print(json.dumps(summary, indent=2))
PY
}

main "$@"
