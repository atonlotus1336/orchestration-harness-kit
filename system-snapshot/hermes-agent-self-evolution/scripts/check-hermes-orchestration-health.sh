#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"

REPO_ROOT="__WSL_WORKSPACE__/hermes-agent"
HERMES_BIN="${HERMES_BIN:-$USER_HOME/.hermes/hermes-agent/venv/bin/hermes}"
LOG_DIR="$USER_HOME/.hermes/logs"
LOG_FILE="$LOG_DIR/orchestration-health.log"
REQUIRE_HERMES_GUARDRAILS_READY="${REQUIRE_HERMES_GUARDRAILS_READY:-0}"

mkdir -p "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
}

say() {
  printf '%s\n' "$*"
  log "$*"
}

status=0

if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  say "Hermes orchestration checks degraded: $REPO_ROOT is not a git repo"
  exit 0
fi

if ! git -C "$REPO_ROOT" config core.hooksPath .githooks >/dev/null 2>&1; then
  say "Hermes orchestration checks degraded: could not set core.hooksPath to .githooks"
  status=1
fi

chmod +x "$REPO_ROOT/.githooks/pre-commit" "$REPO_ROOT/.githooks/pre-push" "$REPO_ROOT/scripts/run_tests.sh" "$REPO_ROOT/scripts/run_hook_health_checks.sh" "$REPO_ROOT/scripts/install_git_hooks.sh" "$REPO_ROOT/scripts/check_repo_guards.sh" >/dev/null 2>&1 || true
chmod +x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-optimizer.sh >/dev/null 2>&1 || true

if ! __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-optimizer.sh >> "$LOG_FILE" 2>&1; then
  say "Hermes optimizer install check degraded. See $LOG_FILE"
  status=1
fi

if ! bash "$REPO_ROOT/scripts/install_git_hooks.sh" >> "$LOG_FILE" 2>&1; then
  say "Hermes orchestration checks degraded: could not install repo git hooks. See $LOG_FILE"
  status=1
fi

if ! bash "$REPO_ROOT/scripts/check_repo_guards.sh" --bootstrap --startup >> "$LOG_FILE" 2>&1; then
  say "Hermes guardrail wiring degraded. See $LOG_FILE"
  status=1
fi

if ! "$REPO_ROOT/scripts/run_hook_health_checks.sh" >> "$LOG_FILE" 2>&1; then
  say "Hermes hook regression checks degraded. See $LOG_FILE"
  status=1
fi

if [ -f __WSL_WORKSPACE__/.mcp-servers/wordpress/sync_all_hermes_profiles.py ]; then
  "$USER_HOME/.hermes/hermes-agent/venv/bin/python" \
    __WSL_WORKSPACE__/.mcp-servers/wordpress/sync_all_hermes_profiles.py >> "$LOG_FILE" 2>&1 || true
fi

for server in obsidian memoryweaver-mcp rag-orchestrator wordpress; do
  if ! HERMES_HOME="$USER_HOME/.hermes/profiles/home" "$HERMES_BIN" mcp test "$server" >> "$LOG_FILE" 2>&1; then
    say "Hermes orchestration MCP check degraded for $server. See $LOG_FILE"
    status=1
  fi
done

if [ "$status" -eq 0 ]; then
  say "Hermes orchestration checks ready: hooks, lint guardrails, hook regressions, and MCP endpoints passed"
  exit 0
fi

if [ "$REQUIRE_HERMES_GUARDRAILS_READY" = "1" ]; then
  exit 1
fi

exit 0
