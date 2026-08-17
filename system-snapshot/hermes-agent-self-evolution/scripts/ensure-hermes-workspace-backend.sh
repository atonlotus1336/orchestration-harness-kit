#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"
LOG_DIR="$USER_HOME/.hermes/logs"
mkdir -p "$LOG_DIR"

__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-api.sh || true
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-ollama-models-to-hermes.sh || true

if ! curl -sf http://127.0.0.1:9119/api/status >/dev/null 2>&1; then
  # 2026-07-30: pin the dashboard session token when a token file exists, so remote
  # clients (the desktop app on DESKTOP-2S7VQDO over the SSH tunnel) can authenticate.
  # Without this the dashboard mints a random token per start and no client can pair.
  # The file lives in WSL-only home — never on G: (key-never-on-G invariant).
  if [ -f "$USER_HOME/.hermes/dashboard-session-token" ]; then
    HERMES_DASHBOARD_SESSION_TOKEN="$(cat "$USER_HOME/.hermes/dashboard-session-token")"
    export HERMES_DASHBOARD_SESSION_TOKEN
  fi
  nohup "$USER_HOME/.hermes/hermes-agent/venv/bin/hermes" dashboard --no-open --host 127.0.0.1 --port 9119 >> "$LOG_DIR/dashboard.log" 2>&1 &
fi

exec sleep infinity
