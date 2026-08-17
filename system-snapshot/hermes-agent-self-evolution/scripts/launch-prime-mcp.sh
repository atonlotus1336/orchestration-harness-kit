#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="__WSL_WORKSPACE__/hermes-agent-self-evolution"
HERMES_PYTHON="${HERMES_PYTHON:-/home/__LINUX_USER__/.hermes/hermes-agent/venv/bin/python}"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$HERMES_PYTHON" -m prime_bridge.mcp_server
