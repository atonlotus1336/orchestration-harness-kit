#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"

HERMES_BIN="${HERMES_BIN:-$USER_HOME/.hermes/hermes-agent/venv/bin/hermes}"
ROOT_HOME="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
ACTIVE_PROFILE="$(cat "$ROOT_HOME/active_profile" 2>/dev/null || true)"
ACTIVE_PROFILE="${ACTIVE_PROFILE:-home}"
PROFILE_HOME="$ROOT_HOME/profiles/$ACTIVE_PROFILE"
LOG_DIR="$ROOT_HOME/logs"
LOG_FILE="$LOG_DIR/startup-auth.log"

mkdir -p "$PROFILE_HOME" "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
}

python3 - "$ROOT_HOME" "$PROFILE_HOME" "$LOG_FILE" <<'PY'
import json
import os
import re
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
profile = Path(sys.argv[2])
log_file = Path(sys.argv[3])

def log(message: str) -> None:
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"startup-auth: failed to parse {path}: {exc}")
        return {}

def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + ".bak-codex-startup-auth")
        if not backup.exists():
            shutil.copy2(path, backup)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass

def merge_auth() -> None:
    root_auth = load_json(root / "auth.json")
    if not root_auth:
        log("startup-auth: root auth.json missing or empty; nothing to sync")
        return

    profile_auth_path = profile / "auth.json"
    profile_auth = load_json(profile_auth_path)
    changed = False

    for key in ("version", "updated_at"):
        if key in root_auth and profile_auth.get(key) != root_auth[key]:
            profile_auth[key] = root_auth[key]
            changed = True

    for section in ("providers", "credential_pool"):
        src = root_auth.get(section)
        if not isinstance(src, dict):
            continue
        dst = profile_auth.get(section)
        if not isinstance(dst, dict):
            dst = {}
            profile_auth[section] = dst
            changed = True
        for provider, value in src.items():
            existing = dst.get(provider)
            if existing in (None, {}, []):
                dst[provider] = value
                changed = True

    if "active_provider" in root_auth and not profile_auth.get("active_provider"):
        profile_auth["active_provider"] = root_auth["active_provider"]
        changed = True

    if changed:
        save_json(profile_auth_path, profile_auth)
        log(f"startup-auth: synced auth providers into {profile_auth_path}")
    else:
        log("startup-auth: active profile auth already has root providers")

def sync_file(name: str) -> None:
    src = root / name
    dst = profile / name
    if src.exists() and not dst.exists():
        shutil.copy2(src, dst)
        try:
            dst.chmod(0o600)
        except Exception:
            pass
        log(f"startup-auth: copied {name} into active profile")

def sync_env_keys() -> None:
    src = root / ".env"
    dst = profile / ".env"
    if not src.exists():
        return
    allowed = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OLLAMA_API_KEY",
        "OLLAMA_BASE_URL",
        "OPENROUTER_API_KEY",
        "API_SERVER_ENABLED",
        "API_SERVER_HOST",
        "API_SERVER_PORT",
        "API_SERVER_KEY",
        "API_SERVER_MODEL_NAME",
        "COMFYUI_WINDOWS_ROOT",
        "COMFYUI_WINDOWS_BATCH",
        "COMFYUI_HOST",
        "COMFYUI_PORT",
        "COMFYUI_DEFAULT_CHECKPOINT",
        "COMFYUI_OUTPUT_DIR",
        "COMFYUI_WORKFLOW_DIR",
        "COMFYUI_START_WAIT_SECONDS",
        "COMFYUI_BRIDGE_STATE_PATH",
        "COMFYUI_BRIDGE_LOG_PATH",
        "COMFYUI_CONTRACT_PATH",
        "COMFYUI_API_RETRY_ATTEMPTS",
        "COMFYUI_API_RETRY_DELAY_SECONDS",
        "COMFYUI_OUTPUT_SETTLE_TIMEOUT_SECONDS",
        "COMFYUI_WATCH_DIR",
        "COMFYUI_WATCH_EXTENSIONS",
        "COMFYUI_WATCH_MIN_BYTES",
        "COMFYUI_WATCH_SETTLE_SECONDS",
        "COMFYUI_WATCH_POLL_SECONDS",
    }
    src_values = {}
    for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in allowed and value:
            src_values[key] = value
    if not src_values:
        return

    lines = dst.read_text(encoding="utf-8", errors="ignore").splitlines() if dst.exists() else []
    out = []
    seen = set()
    changed = False
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key, value = line.split("=", 1)
        if key in src_values:
            new_line = f"{key}={src_values[key]}"
            out.append(new_line)
            seen.add(key)
            if line != new_line:
                changed = True
        else:
            out.append(line)
    additions = [f"{key}={value}" for key, value in src_values.items() if key not in seen]
    if additions:
        out.extend(additions)
        changed = True
    if changed or not dst.exists():
        if dst.exists():
            backup = dst.with_name(dst.name + ".bak-codex-startup-auth")
            if not backup.exists():
                shutil.copy2(dst, backup)
        dst.write_text("\n".join(out) + "\n", encoding="utf-8")
        try:
            dst.chmod(0o600)
        except Exception:
            pass
        copied = additions if additions else [line for line in out if line.split("=", 1)[0] in src_values]
        log(f"startup-auth: synced env keys into {dst}: {', '.join(line.split('=', 1)[0] for line in copied)}")

def sync_orchestration_config() -> None:
    root_config = root / "config.yaml"
    profile_config = profile / "config.yaml"
    if not root_config.exists() or not profile_config.exists():
        return

    def top_level_block(text: str, key: str) -> str:
        match = re.search(rf"^{re.escape(key)}:\n(?:^[ \t].*\n|^\n)*", text, re.M)
        return match.group(0).rstrip() + "\n" if match else ""

    def replace_top_level_block(text: str, key: str, block: str) -> str:
        if not block:
            return text
        pattern = rf"^{re.escape(key)}:\n(?:^[ \t].*\n|^\n)*"
        if re.search(pattern, text, re.M):
            return re.sub(pattern, lambda _match: block, text, count=1, flags=re.M)
        separator = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{separator}{block}"

    root_text = root_config.read_text(encoding="utf-8", errors="ignore")
    text = profile_config.read_text(encoding="utf-8", errors="ignore")
    original = text
    for key in ("providers", "mcp_servers", "memory", "toolsets"):
        text = replace_top_level_block(text, key, top_level_block(root_text, key))
    if text != original:
        backup = profile_config.with_name(profile_config.name + ".bak-codex-orchestration")
        if not backup.exists():
            shutil.copy2(profile_config, backup)
        profile_config.write_text(text, encoding="utf-8")
        log(f"startup-auth: synced global orchestration config into {profile_config}")

def write_oauth_tokens(token: str) -> None:
    for dst in (root / ".env", profile / ".env"):
        lines = dst.read_text(encoding="utf-8", errors="ignore").splitlines() if dst.exists() else []
        next_lines = []
        seen = set()
        for line in lines:
            if "=" not in line or line.lstrip().startswith("#"):
                next_lines.append(line)
                continue
            key, _value = line.split("=", 1)
            if key in {"ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"}:
                next_lines.append(f"{key}={token}")
                seen.add(key)
            else:
                next_lines.append(line)
        for key in ("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            if key not in seen:
                next_lines.append(f"{key}={token}")
        if dst.exists():
            backup = dst.with_name(dst.name + ".bak-codex-startup-auth")
            if not backup.exists():
                shutil.copy2(dst, backup)
        dst.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
        try:
            dst.chmod(0o600)
        except Exception:
            pass

def clear_oauth_tokens() -> None:
    for dst in (root / ".env", profile / ".env"):
        if not dst.exists():
            continue
        lines = dst.read_text(encoding="utf-8", errors="ignore").splitlines()
        next_lines = []
        changed = False
        for line in lines:
            if "=" in line and not line.lstrip().startswith("#"):
                key, _value = line.split("=", 1)
                if key in {"ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"}:
                    changed = True
                    continue
            next_lines.append(line)
        if changed:
            backup = dst.with_name(dst.name + ".bak-codex-startup-auth")
            if not backup.exists():
                shutil.copy2(dst, backup)
            dst.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
            try:
                dst.chmod(0o600)
            except Exception:
                pass

def token_looks_valid(token: str) -> bool:
    import urllib.error
    import urllib.request

    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= int(status) < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False
        log(f"startup-auth: Anthropic token probe returned HTTP {exc.code}; leaving token in place")
        return True
    except Exception as exc:
        log(f"startup-auth: Anthropic token probe failed unexpectedly: {exc}; leaving token in place")
        return True

def import_claude_oauth() -> None:
    credentials = Path.home() / ".claude" / ".credentials.json"
    if not credentials.exists():
        log("startup-auth: Claude CLI credentials not found; Anthropic OAuth not imported")
        clear_oauth_tokens()
        return
    try:
        data = json.loads(credentials.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"startup-auth: failed to parse Claude CLI credentials: {exc}")
        return
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    token = oauth.get("accessToken") if isinstance(oauth, dict) else ""
    if not token:
        log("startup-auth: Claude CLI access token missing; Anthropic OAuth not imported")
        clear_oauth_tokens()
        return

    write_oauth_tokens(token)
    if token_looks_valid(token):
        log("startup-auth: imported Claude CLI OAuth token into Hermes env files")
    else:
        clear_oauth_tokens()
        log("startup-auth: Claude CLI OAuth token is invalid; cleared Anthropic env tokens to avoid partial startup auth")

def sync_windows_workspace_profile() -> None:
    windows_root = Path(os.environ.get("HERMES_WINDOWS_ROOT", "/mnt/c/Users/__WINDOWS_USER__/.hermes"))
    if not windows_root.exists():
        return
    active_profile = windows_root / "active_profile"
    current = active_profile.read_text(encoding="utf-8", errors="ignore").strip() if active_profile.exists() else ""
    profiles_root = windows_root / "profiles"
    current_exists = bool(current) and (profiles_root / current / "config.yaml").exists()
    preferred = "GPT" if (profiles_root / "GPT" / "config.yaml").exists() else "default"
    target = current if current_exists and current != "default" else preferred
    if current != target:
        backup = active_profile.with_name(active_profile.name + ".bak-codex-startup-auth")
        if active_profile.exists() and not backup.exists():
            shutil.copy2(active_profile, backup)
        active_profile.write_text(f"{target}\n", encoding="utf-8")
        log(f"startup-auth: selected {target} Windows Workspace profile")

def import_windows_codex_auth() -> None:
    codex_auth = Path(os.environ.get("WINDOWS_CODEX_AUTH", "/mnt/c/Users/__WINDOWS_USER__/.codex/auth.json"))
    if not codex_auth.exists():
        log("startup-auth: Windows Codex auth.json missing; OpenAI Codex not imported")
        return
    try:
        source = json.loads(codex_auth.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"startup-auth: failed to parse Windows Codex auth.json: {exc}")
        return
    tokens = source.get("tokens") if isinstance(source, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token") or not tokens.get("refresh_token"):
        log("startup-auth: Windows Codex auth.json has no usable access/refresh token pair")
        return

    auth_path = profile / "auth.json"
    current = load_json(auth_path)
    current.setdefault("version", 1)
    current["updated_at"] = source.get("last_refresh") or current.get("updated_at") or ""
    current["active_provider"] = "openai-codex"

    providers = current.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        current["providers"] = providers
    providers["openai-codex"] = {
        "auth_mode": source.get("auth_mode") or "chatgpt",
        "tokens": tokens,
        "last_refresh": source.get("last_refresh") or current["updated_at"],
    }

    pool = current.get("credential_pool")
    if not isinstance(pool, dict):
        pool = {}
        current["credential_pool"] = pool
    entries = pool.get("openai-codex")
    if not isinstance(entries, list):
        entries = []
    imported = {
        "id": "windows-codex-import",
        "label": "Windows Codex import",
        "type": "oauth",
        "auth_mode": source.get("auth_mode") or "chatgpt",
        "tokens": tokens,
        "last_refresh": source.get("last_refresh") or current["updated_at"],
        "created_at": current["updated_at"],
        "updated_at": current["updated_at"],
    }
    pool["openai-codex"] = [imported] + [
        entry
        for entry in entries
        if not (isinstance(entry, dict) and entry.get("id") == "windows-codex-import")
    ]

    save_json(auth_path, current)
    log(f"startup-auth: imported Windows Codex login into {auth_path}")

    windows_profile_auth = Path(os.environ.get("HERMES_WINDOWS_ROOT", "/mnt/c/Users/__WINDOWS_USER__/.hermes")) / "profiles" / profile.name / "auth.json"
    if windows_profile_auth != auth_path:
        save_json(windows_profile_auth, current)
        log(f"startup-auth: imported Windows Codex login into {windows_profile_auth}")

merge_auth()
sync_file(".anthropic_oauth.json")
sync_env_keys()
sync_orchestration_config()
import_claude_oauth()
sync_windows_workspace_profile()
import_windows_codex_auth()
PY

# Global orchestration sync intentionally replaces the complete mcp_servers block.
# Reapply the centrally governed Prime overlay afterward so every activated profile
# keeps the same restart-boundary policy instead of losing it on startup.
PYTHONPATH="__WSL_WORKSPACE__/hermes-agent-self-evolution${PYTHONPATH:+:$PYTHONPATH}" \
  python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/render-prime-profile-overlay.py \
  --profiles-root "$ROOT_HOME/profiles" \
  --profile "$ACTIVE_PROFILE" \
  --policy __WSL_WORKSPACE__/hermes-agent-self-evolution/config/prime-profile-policy.json \
  --ledger "$ROOT_HOME/state/prime/jobs.sqlite3" \
  --in-place

ACTIVE_PROFILE="$(cat "$ROOT_HOME/active_profile" 2>/dev/null || true)"
ACTIVE_PROFILE="${ACTIVE_PROFILE:-home}"
PROFILE_HOME="$ROOT_HOME/profiles/$ACTIVE_PROFILE"
mkdir -p "$PROFILE_HOME"

if [ -x "$HERMES_BIN" ]; then
  for provider in openai-codex anthropic ollama-cloud custom:ollama-cloud custom:ollama-local custom:ollama-cloud-local; do
    status="$(HERMES_HOME="$PROFILE_HOME" "$HERMES_BIN" auth status "$provider" 2>&1 || true)"
    log "auth status $provider: $status"
  done

  codex_status="$(HERMES_HOME="$PROFILE_HOME" "$HERMES_BIN" auth status openai-codex 2>&1 || true)"
  if printf '%s\n' "$codex_status" | grep -qi "logged in"; then
    rm -f "$ROOT_HOME/logs/codex-auth-action-required.txt"
    if [ "$ACTIVE_PROFILE" = "telegram" ]; then
      log "startup-auth: openai-codex logged in, but active profile is telegram; preserving Telegram model routing"
      exit 0
    fi
    if [ "${HERMES_STARTUP_AUTH_PROMOTE_CODEX:-}" != "1" ]; then
      log "startup-auth: openai-codex logged in; preserving configured primary/fallback model routing"
      exit 0
    fi
    python3 - "$PROFILE_HOME/config.yaml" "$ROOT_HOME/config.yaml" "$LOG_FILE" <<'PY'
import shutil
import sys
from pathlib import Path
import yaml

paths = [Path(sys.argv[1]), Path(sys.argv[2])]
windows_root_config = Path(os.environ.get("HERMES_WINDOWS_ROOT", "/mnt/c/Users/__WINDOWS_USER__/.hermes")) / "config.yaml"
if windows_root_config.exists():
    paths.append(windows_root_config)
log_file = Path(sys.argv[3])
for path in paths:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        data = {}
    model = data.get("model")
    if not isinstance(model, dict):
        model = {}
    if model.get("provider") == "openai-codex" and model.get("default") == "gpt-5.4":
        continue
    if path.exists():
        backup = path.with_name(path.name + ".bak-codex-startup-auth")
        if not backup.exists():
            shutil.copy2(path, backup)
    model["provider"] = "openai-codex"
    model["default"] = "gpt-5.4"
    data["model"] = model
    data.pop("provider", None)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(f"startup-auth: selected openai-codex/gpt-5.4 in {path}\n")
PY
  else
    log "openai-codex not logged in for active profile $ACTIVE_PROFILE; leaving current model unchanged"
    cat > "$ROOT_HOME/logs/codex-auth-action-required.txt" <<EOF

CRITICAL: OpenAI Codex auth is currently unusable for Hermes profile "$ACTIVE_PROFILE". This will cause GPT/Codex chat to fail or drift to an incorrect model.

ACTION REQUIRED: Please perform the following to restore functionality:
1. Log in/refresh Codex: Run \`codex\` and \`hermes auth\` in Ubuntu WSL.
2. Restart Workspace: Relaunch with \`__WINDOWS_WORKSPACE__\hermes-workspace.bat\`.

Alternatively, if Codex is not desired, please switch your active profile to a stable/preferred provider (e.g., Ollama/GPT) and restart.

For status details: \`hermes status\`.

EOF
  fi
else
  log "Hermes binary missing at $HERMES_BIN"
fi
