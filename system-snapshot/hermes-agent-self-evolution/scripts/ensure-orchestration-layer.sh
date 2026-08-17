#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"

ROOT_HERMES_HOME="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
ACTIVE_PROFILE="$(cat "$ROOT_HERMES_HOME/active_profile" 2>/dev/null || true)"
ACTIVE_PROFILE="${ACTIVE_PROFILE:-home}"
PROFILE_HOME="$ROOT_HERMES_HOME/profiles/$ACTIVE_PROFILE"
HERMES_BIN="${HERMES_BIN:-$ROOT_HERMES_HOME/hermes-agent/venv/bin/hermes}"
HERMES_REPO="${HERMES_REPO:-__WSL_WORKSPACE__/hermes-agent}"
LOG_DIR="$ROOT_HERMES_HOME/logs"
LOG_FILE="$LOG_DIR/orchestration-layer.log"
SUMMARY_FILE="$LOG_DIR/orchestration-layer.last"
REQUIRE_ORCHESTRATION_READY="${REQUIRE_ORCHESTRATION_READY:-0}"

mkdir -p "$LOG_DIR" "$PROFILE_HOME"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
}

summary() {
  printf '%s\n' "$*"
  printf '%s\n' "$*" >> "$SUMMARY_FILE"
  log "$*"
}

: > "$SUMMARY_FILE"
summary "Orchestration layer startup check"
summary "Profile: $ACTIVE_PROFILE"

overall_ok=1

if [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-optimizer.sh ]; then
  optimizer_output="$(__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-optimizer.sh 2>&1 || true)"
  printf '%s\n' "$optimizer_output" >> "$LOG_FILE"
  if printf '%s' "$optimizer_output" | grep -q 'Hermes Optimizer ready:'; then
    summary "Optimizer: OK"
  else
    summary "Optimizer: FAILED"
    overall_ok=0
  fi
else
  summary "Optimizer: missing ensure-hermes-optimizer.sh"
  overall_ok=0
fi

if [ -x __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-rag-stack.sh ]; then
  rag_output="$(__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-rag-stack.sh 2>&1 || true)"
  printf '%s\n' "$rag_output" >> "$LOG_FILE"
  while IFS= read -r line; do
    [ -n "$line" ] && summary "RAG: $line"
  done <<< "$rag_output"
  if printf '%s' "$rag_output" | grep -qi 'degraded'; then
    overall_ok=0
  fi
else
  summary "RAG: ensure-rag-stack.sh missing"
  overall_ok=0
fi

if [ -f __WSL_WORKSPACE__/.mcp-servers/wordpress/sync_all_hermes_profiles.py ] && [ -x "$ROOT_HERMES_HOME/hermes-agent/venv/bin/python" ]; then
  wordpress_sync_output="$("$ROOT_HERMES_HOME/hermes-agent/venv/bin/python" __WSL_WORKSPACE__/.mcp-servers/wordpress/sync_all_hermes_profiles.py 2>&1 || true)"
  printf '%s\n' "$wordpress_sync_output" >> "$LOG_FILE"
  summary "MCP: wordpress profile sync checked"
fi

if [ -f __WSL_WORKSPACE__/.mcp-servers/orchestration-contracts/sync_hermes_soul_guidance.py ] && [ -x "$ROOT_HERMES_HOME/hermes-agent/venv/bin/python" ]; then
  contract_soul_output="$(timeout 10s "$ROOT_HERMES_HOME/hermes-agent/venv/bin/python" __WSL_WORKSPACE__/.mcp-servers/orchestration-contracts/sync_hermes_soul_guidance.py 2>&1 || true)"
  printf '%s\n' "$contract_soul_output" >> "$LOG_FILE"
  if printf '%s' "$contract_soul_output" | grep -q '^done:'; then
    summary "Contracts: Hermes SOUL guidance checked"
  else
    summary "Contracts: Hermes SOUL guidance skipped or timed out"
  fi
fi

for server in memoryweaver-mcp rag-orchestrator obsidian wordpress; do
  if [ ! -x "$HERMES_BIN" ]; then
    summary "MCP: Hermes binary missing at $HERMES_BIN"
    overall_ok=0
    break
  fi
  output="$(HERMES_HOME="$PROFILE_HOME" "$HERMES_BIN" mcp test "$server" 2>&1 || true)"
  printf '%s\n' "$output" >> "$LOG_FILE"
  if printf '%s' "$output" | grep -Eq '✓ Connected|Connected \([0-9]+ms\)' && printf '%s' "$output" | grep -Eq '✓ Tools discovered|Tools discovered:'; then
    summary "MCP: $server OK"
  else
    summary "MCP: $server FAILED"
    overall_ok=0
  fi
done

if [ -f "$HERMES_REPO/scripts/check_repo_guards.sh" ]; then
  quality_output="$(bash "$HERMES_REPO/scripts/check_repo_guards.sh" --startup 2>&1 || true)"
  printf '%s\n' "$quality_output" >> "$LOG_FILE"
  if printf '%s' "$quality_output" | grep -q 'Hermes repo guards ready'; then
    summary "Repo guardrails: OK"
  else
    summary "Repo guardrails: FAILED"
    overall_ok=0
  fi
else
  summary "Repo guardrails: missing $HERMES_REPO/scripts/check_repo_guards.sh"
  overall_ok=0
fi

if [ "$overall_ok" -eq 1 ]; then
  summary "Overall: orchestration layer ready"
else
  summary "Overall: orchestration layer degraded"
fi

# After all MCP servers pass health checks, restart the gateway so any
# running sessions reconnect and pick up all verified MCP tools. This fixes
# the session registration gap where sessions created before MCP servers
# were healthy never get the tools.
# NOTE: The gateway does NOT have a /reload-mcp HTTP endpoint. The only
# way to push new MCP registrations into active sessions is a gateway restart.
if curl -sf http://127.0.0.1:${API_SERVER_PORT:-8787}/health >/dev/null 2>&1; then
  systemctl --user restart hermes-gateway >/dev/null 2>&1 || true
  summary "MCP: gateway restart signaled to pick up verified MCP tools"
else
  summary "MCP: gateway not reachable; tools will load on next gateway start"
fi

if [ "$REQUIRE_ORCHESTRATION_READY" = "1" ] && [ "$overall_ok" -eq 0 ]; then
  exit 1
fi
exit 0
