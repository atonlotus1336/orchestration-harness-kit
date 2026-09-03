#!/usr/bin/env bash
set -euo pipefail
set +x

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"

LOG_DIR="${USER_HOME}/.hermes/logs"
LOG_FILE="${LOG_DIR}/rag-stack.log"
PYTHON_BIN="${RAG_ORCHESTRATOR_PYTHON:-/home/__LINUX_USER__/.local/share/rag-orchestrator/venv/bin/python3.11}"
SERVER_PATH="${RAG_ORCHESTRATOR_SERVER:-__WSL_WORKSPACE__/.mcp-servers/rag-orchestrator/server.py}"
MEMORY_DIR="${MEMORYWEAVER_DATA_DIR:-__WSL_WORKSPACE__/.claude/memory/memoryweaver}"
GRAPH_INDEX_BAT='__WINDOWS_WORKSPACE__\.rag\index-graphrag.bat'
GRAPH_INDEX_STAMP="${USER_HOME}/.hermes/graphrag-index.last-started"
GRAPH_INDEX_COOLDOWN_SECONDS="${GRAPH_INDEX_COOLDOWN_SECONDS:-21600}"
# Keep retries bounded while allowing Memoryweaver enough time to recover
# after reboot or disk repair.
STACK_READY_ATTEMPTS="${RAG_STACK_READY_ATTEMPTS:-8}"
STACK_READY_DELAY_SECONDS="${RAG_STACK_READY_DELAY_SECONDS:-4}"
AUTO_INDEX_GRAPHRAG="${AUTO_INDEX_GRAPHRAG:-0}"
REQUIRE_RAG_READY="${REQUIRE_RAG_READY:-0}"

mkdir -p "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
}

say() {
  printf '%s\n' "$*"
  log "$*"
}

probe_status() {
  MEMORYWEAVER_DATA_DIR="$MEMORY_DIR" \
  RAG_BACKEND_RETRY_ATTEMPTS="${RAG_BACKEND_RETRY_ATTEMPTS:-3}" \
  RAG_BACKEND_RETRY_DELAY_SECONDS="${RAG_BACKEND_RETRY_DELAY_SECONDS:-1}" \
  "$PYTHON_BIN" - "$SERVER_PATH" <<'PY'
import asyncio
import importlib.util
import json
import sys

server_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("rag_orchestrator", server_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

async def main():
    statuses = {}
    for backend in mod.registry.all():
        statuses[backend.name] = await mod._retry_backend_status(backend)
    print(json.dumps(statuses, sort_keys=True))

asyncio.run(main())
PY
}

backend_available() {
  local status_json="$1"
  local backend_name="$2"
  STATUS_JSON="$status_json" python3 - "$backend_name" <<'PY'
import json
import os
import sys

backend = sys.argv[1]
status = json.loads(os.environ["STATUS_JSON"])
state = status.get(backend, {})
raw = state.get("raw_status") or {}
healthy = (
    state.get("available") is True
    or state.get("primary_ok") is True
    or state.get("status") == "healthy"
    or raw.get("available") is True
)
print("true" if healthy else "false")
PY
}

graphrag_missing_outputs() {
  local status_json="$1"
  STATUS_JSON="$status_json" python3 - <<'PY'
import json
import os

status = json.loads(os.environ["STATUS_JSON"])
graph = status.get("graphrag", {})
missing = graph.get("missing_required_outputs") or []
print("true" if missing else "false")
PY
}

start_graphrag_index() {
  if ! command -v powershell.exe >/dev/null 2>&1; then
    log "GraphRAG incomplete and powershell.exe is not available from WSL; cannot auto-start indexing"
    return 0
  fi

  local now stamp_age=999999999
  now="$(date +%s)"
  if [ -f "$GRAPH_INDEX_STAMP" ]; then
    stamp_age=$(( now - $(stat -c %Y "$GRAPH_INDEX_STAMP") ))
  fi

  if [ "$stamp_age" -lt "$GRAPH_INDEX_COOLDOWN_SECONDS" ]; then
    log "GraphRAG incomplete but index auto-start is cooling down (${stamp_age}s < ${GRAPH_INDEX_COOLDOWN_SECONDS}s)"
    return 0
  fi

  printf '%s\n' "$now" > "$GRAPH_INDEX_STAMP"
  log "GraphRAG incomplete; attempting Windows background index run"
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \
    "Start-Process -WindowStyle Hidden -FilePath 'cmd.exe' -ArgumentList '/c','set NO_PAUSE=1 && ${GRAPH_INDEX_BAT}'" \
    >/dev/null 2>&1 || log "GraphRAG auto-index command failed"
}

status="$(probe_status 2>&1 || true)"
log "initial rag_status: $status"

for attempt in $(seq 1 "$STACK_READY_ATTEMPTS"); do
  memory_ok="$(backend_available "$status" memoryweaver 2>/dev/null || echo false)"

  if [ "$memory_ok" = "true" ]; then
    break
  fi

  if [ "$attempt" -lt "$STACK_READY_ATTEMPTS" ]; then
    sleep "$STACK_READY_DELAY_SECONDS"
    status="$(probe_status 2>&1 || true)"
    log "retry ${attempt}/${STACK_READY_ATTEMPTS} rag_status: $status"
  fi
done

if [ "$AUTO_INDEX_GRAPHRAG" = "1" ] && [ "$(graphrag_missing_outputs "$status" 2>/dev/null || echo false)" = "true" ]; then
  start_graphrag_index
fi

memory_ok="$(backend_available "$status" memoryweaver 2>/dev/null || echo false)"

if [ "$memory_ok" = "true" ]; then
  say "RAG baseline ready: Memoryweaver is available"
else
  say "RAG baseline degraded after retries. See $LOG_FILE for details."
  if [ "$REQUIRE_RAG_READY" = "1" ]; then
    exit 1
  fi
fi

if [ "$(graphrag_missing_outputs "$status" 2>/dev/null || echo false)" = "true" ]; then
  if [ "$AUTO_INDEX_GRAPHRAG" = "1" ]; then
    say "GraphRAG degraded: background index attempt requested; rerun startup later to verify completion"
  else
    say "GraphRAG degraded: index incomplete; run __WINDOWS_WORKSPACE__\\.rag\\index-graphrag.bat deliberately when a long rebuild is acceptable"
  fi
else
  rm -f "$GRAPH_INDEX_STAMP"
  say "GraphRAG ready"
fi

exit 0
