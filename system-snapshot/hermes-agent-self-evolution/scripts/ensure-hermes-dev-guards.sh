#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"

REPO_ROOT="__WSL_WORKSPACE__/hermes-agent"
GUARD_SCRIPT="$REPO_ROOT/scripts/check_repo_guards.sh"
LOG_DIR="$HOME/.hermes/logs"
LOG_FILE="$LOG_DIR/dev-guards.log"
SELF_FILES=(
  "__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-startup-auth.sh"
  "__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-rag-stack.sh"
  "__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-api.sh"
  "__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-workspace-backend.sh"
  "__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-dev-guards.sh"
)

mkdir -p "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
}

say() {
  printf '%s\n' "$*"
  log "$*"
}

if [ ! -x "$GUARD_SCRIPT" ]; then
  say "Hermes repo guards degraded: missing $GUARD_SCRIPT"
  exit 1
fi

for file in "${SELF_FILES[@]}"; do
  if [ ! -f "$file" ]; then
    say "Hermes repo guards degraded: missing $file"
    exit 1
  fi
done

if ! bash -n "${SELF_FILES[@]}" >> "$LOG_FILE" 2>&1; then
  say "Hermes repo guards degraded: shell syntax check failed; see $LOG_FILE"
  exit 1
fi

if "$GUARD_SCRIPT" --bootstrap --startup >> "$LOG_FILE" 2>&1; then
  say "Hermes repo guards ready"
  exit 0
fi

say "Hermes repo guards degraded: repo checks failed; see $LOG_FILE"
exit 1
