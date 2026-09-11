#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="__WSL_WORKSPACE__/hermes-agent-self-evolution"
PRIME_MCP_PYTHON="__WSL_WORKSPACE__/hermes-agent-self-evolution/.venv/bin/python"
export LAPART_EXECUTOR_DB="${LAPART_EXECUTOR_DB:-__WSL_WORKSPACE__/.state/governed-executor/tasks.sqlite3}"
export LAPART_GOVERNED_EXECUTOR_SRC="${LAPART_GOVERNED_EXECUTOR_SRC:-__WSL_WORKSPACE__/governed-executor-runtime/src}"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Hermes profile files are parsed by the host but are not automatically
# exported to stdio MCP subprocesses. Import only the adaptive qualification
# authority, without sourcing or evaluating the rest of the profile file.
PRIME_PROFILE_ENV=""
if [[ -n "${HERMES_PRIME_PROFILE_CONFIG:-}" ]]; then
  PRIME_PROFILE_ENV="${HERMES_PRIME_PROFILE_CONFIG%/*}/.env"
fi
if [[ -z "${LAPART_QUALIFICATION_HMAC_KEY:-}" && -r "$PRIME_PROFILE_ENV" ]]; then
  while IFS='=' read -r name value; do
    if [[ "$name" == "LAPART_QUALIFICATION_HMAC_KEY" && -n "$value" ]]; then
      export LAPART_QUALIFICATION_HMAC_KEY="$value"
      break
    fi
  done < "$PRIME_PROFILE_ENV"
fi
QUALIFICATION_AUTHORITY_STATUS="unavailable"
if [[ -n "${LAPART_QUALIFICATION_HMAC_KEY:-}" ]]; then
  QUALIFICATION_AUTHORITY_STATUS="available"
fi
if [[ "${1:-}" == "--print-effective-config" ]]; then
  printf '{"interpreter":"%s","executor_db":"%s","executor_src":"%s","qualification_authority":"%s"}\n' \
    "$PRIME_MCP_PYTHON" "$LAPART_EXECUTOR_DB" "$LAPART_GOVERNED_EXECUTOR_SRC" \
    "$QUALIFICATION_AUTHORITY_STATUS"
  exit 0
fi
exec "$PRIME_MCP_PYTHON" -m prime_bridge.mcp_server
