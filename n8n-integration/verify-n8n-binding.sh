#!/usr/bin/env bash
# M13.4 client binding verifier (kit-shareable, POSIX twin of verify-n8n-binding.ps1).
# Exit codes: 0 live | 3 unreachable (outage/retryable) | 1 unhealthy | 4 binding missing.
# Fail-closed: no default endpoint, no shipped credentials.
set -u
BINDINGS="${1:-bindings.local.yaml}"
TIMEOUT="${VERIFY_N8N_TIMEOUT:-20}"

if [ ! -f "$BINDINGS" ]; then
  echo "VERIFY-N8N: BINDING_MISSING - create bindings.local.yaml with an n8n_endpoint slot (see N8N-INTEGRATION-TEMPLATE.md)"
  exit 4
fi
ENDPOINT=$(sed -n 's/^[[:space:]]*n8n_endpoint:[[:space:]]*"\{0,1\}\([^"#[:space:]]*\).*/\1/p' "$BINDINGS" | head -1)
KEYFILE=$(sed -n 's/^[[:space:]]*n8n_api_key_file:[[:space:]]*"\{0,1\}\([^"#[:space:]]*\).*/\1/p' "$BINDINGS" | head -1)
if [ -z "$ENDPOINT" ]; then
  echo "VERIFY-N8N: BINDING_MISSING - n8n_endpoint slot is empty (fail-closed)"
  exit 4
fi

HDR=()
if [ -n "$KEYFILE" ] && [ -f "$KEYFILE" ]; then
  HDR=(-H "X-N8N-API-KEY: $(tr -d '\r\n' < "$KEYFILE")")
fi

# ${HDR[@]+...} guard: empty-array expansion under set -u aborts on bash 3.2 (macOS)
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" ${HDR[@]+"${HDR[@]}"} "${ENDPOINT%/}/api/v1/workflows?limit=1" 2>/dev/null)
if [ "$CODE" = "000" ]; then
  echo "VERIFY-N8N: UNREACHABLE - endpoint did not answer (outage or wrong endpoint); integration must NOT be marked live"
  exit 3
elif [ "$CODE" = "200" ]; then
  echo "VERIFY-N8N: LIVE - endpoint answered and API responded; integration may be marked live"
  exit 0
else
  echo "VERIFY-N8N: UNHEALTHY - endpoint answered but API failed (http=$CODE); check API key file and n8n service"
  exit 1
fi
