#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"
ROOT_HERMES_HOME="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
HERMES_BIN="${HERMES_BIN:-$ROOT_HERMES_HOME/hermes-agent/venv/bin/hermes}"
mkdir -p "$ROOT_HERMES_HOME/logs"

if [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-global-hermes-shared-skills.sh ]; then
    __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-global-hermes-shared-skills.sh
fi

if [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-g-managed-telegram-profile.sh ]; then
    __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-g-managed-telegram-profile.sh
fi

if [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh ]; then
    profile_spec="$(
        python3 - <<'PY'
from pathlib import Path
import os
import yaml

root = Path(os.environ.get("HERMES_WINDOWS_ROOT", "/mnt/c/Users/__WINDOWS_USER__/.hermes"))
active_path = root / "active_profile"
profile = active_path.read_text(encoding="utf-8", errors="ignore").strip() if active_path.exists() else ""
if not profile:
    raise SystemExit(0)

config_path = root / "profiles" / profile / "config.yaml"
if not config_path.exists():
    raise SystemExit(0)

data = yaml.safe_load(config_path.read_text(encoding="utf-8", errors="ignore")) or {}
model_config = data.get("model") if isinstance(data, dict) else None
provider = ""
model = ""
if isinstance(model_config, dict):
    provider = str(model_config.get("provider") or "")
    model = str(model_config.get("default") or model_config.get("model") or "")
elif isinstance(model_config, str):
    model = model_config
provider = provider or (str(data.get("provider") or "") if isinstance(data, dict) else "")
if model:
    print(f"{profile}\t{model}\t{provider}")
PY
    )"
    if [ -n "$profile_spec" ]; then
        IFS="$(printf '\t')" read -r win_profile win_model win_provider <<EOF
$profile_spec
EOF
        HERMES_PROFILE_SYNC_NO_RESTART=1 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh "$win_profile" "$win_model" "$win_provider"
    fi
fi

if [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-startup-auth.sh ]; then
    __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-startup-auth.sh
fi

if [ -f __WSL_WORKSPACE__/.mcp-servers/wordpress/sync_all_hermes_profiles.py ]; then
    "$ROOT_HERMES_HOME/hermes-agent/venv/bin/python" \
      __WSL_WORKSPACE__/.mcp-servers/wordpress/sync_all_hermes_profiles.py >/dev/null 2>&1 || true
fi

if [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-orchestration-layer.sh ]; then
    __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-orchestration-layer.sh || true
elif [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-rag-stack.sh ]; then
    __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-rag-stack.sh || true
fi

if [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/check-hermes-orchestration-health.sh ]; then
    __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/check-hermes-orchestration-health.sh || true
fi

python3 - "$ROOT_HERMES_HOME" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1]) / ".env"
path.parent.mkdir(parents=True, exist_ok=True)
lines = path.read_text(errors="ignore").splitlines() if path.exists() else []
updates = {
    "API_SERVER_ENABLED": "true",
    "API_SERVER_HOST": "127.0.0.1",
    "API_SERVER_PORT": "8642",
}
out = []
seen = set()
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
PY

# Gateway lifecycle: if already running, signal reload; if not, start fresh.
# This runs AFTER all orchestration health checks so MCP tools are registered
# before any new session is created.
WORKSPACE_PROFILE="${HERMES_WORKSPACE_PROFILE:-workspace}"

# Read the port from the profile that will actually be launched, instead of
# assuming 8642. When the workspace profile was moved to 8644 (2026-07-29) this
# hardcoded 8642 made the running gateway invisible here, so this script started
# a SECOND gateway for the same profile every launch, and the caller then fell
# through to the crippled Windows backend. Derive it; never assume it.
GATEWAY_PORT="$(
    { grep -hoP '^API_SERVER_PORT=\K\d+' "$ROOT_HERMES_HOME/profiles/$WORKSPACE_PROFILE/.env" 2>/dev/null || true; } | tail -n1
)"
GATEWAY_PORT="${GATEWAY_PORT:-8642}"
GATEWAY_URL="${HERMES_GATEWAY_URL:-http://127.0.0.1:${GATEWAY_PORT}}"

if curl -sf "${GATEWAY_URL}/health" >/dev/null 2>&1; then
    curl -sf -X POST "${GATEWAY_URL}/reload-mcp" >/dev/null 2>&1 || true
    echo "Gateway already running on ${GATEWAY_URL}; sent MCP reload signal" >&2
    exit 0
fi

# A gateway for this profile may be mid-boot (MCP connection alone takes several
# seconds) and therefore not yet bound. Starting a second one would race the
# first for the port and leave whichever loses dead. Check for the PROCESS, not
# just the port, and wait for the one that already exists.
if pgrep -f -- "--profile ${WORKSPACE_PROFILE} gateway run" >/dev/null 2>&1; then
    echo "Gateway process for profile '${WORKSPACE_PROFILE}' already starting; waiting instead of starting a second one" >&2
    for _ in $(seq 1 30); do
        if curl -sf "${GATEWAY_URL}/health" >/dev/null 2>&1; then
            curl -sf -X POST "${GATEWAY_URL}/reload-mcp" >/dev/null 2>&1 || true
            echo "Gateway became reachable on ${GATEWAY_URL}" >&2
            exit 0
        fi
        sleep 1
    done
    echo "Gateway process for '${WORKSPACE_PROFILE}' exists but never bound ${GATEWAY_URL}" >&2
    exit 1
fi

# No gateway running — start one now.
#
# SAFETY (2026-07-21): this MUST pin --profile and MUST NOT pass --replace.
# A bare `hermes gateway run --replace` resolves HERMES_HOME from
# ~/.hermes/active_profile (which is `telegram`) and therefore SIGTERM/SIGKILLs
# the LIVE Telegram bot's gateway. Pinning the workspace profile scopes both the
# HERMES_HOME and any PID lookup to profiles/<workspace>/, so the Telegram
# gateway on 8643 is never touched. --replace is dropped deliberately: there is
# no workspace gateway to displace, and without it a stale lock makes this
# refuse rather than kill.
systemctl --user restart hermes-gateway >/dev/null 2>&1 || (
    nohup "$HERMES_BIN" --profile "$WORKSPACE_PROFILE" gateway run \
      >> "$ROOT_HERMES_HOME/logs/gateway-workspace.log" 2>&1 &
)

# Seven MCP servers connect serially before the port binds; 10s was shorter than
# a normal cold start, so this reported failure while the gateway was healthy.
for _ in $(seq 1 45); do
    curl -sf "${GATEWAY_URL}/health" >/dev/null 2>&1 && exit 0
    sleep 1
done

echo "Hermes API gateway did not become reachable on ${GATEWAY_URL}" >&2
exit 1
