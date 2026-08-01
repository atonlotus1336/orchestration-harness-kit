#!/usr/bin/env bash
set -euo pipefail

need() { command -v "$1" >/dev/null 2>&1; }

status=PASS
printf 'os=%s\n' "$(uname -s)"
if need python3 && python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  echo "python3.11+=PASS"
else
  echo "python3.11+=FAIL"
  status=FAIL
fi

if need node; then echo "node=PASS"; else echo "node=FAIL"; status=FAIL; fi
if need ollama; then echo "ollama=PASS"; else echo "ollama=DEGRADED"; fi
if need nvidia-smi; then echo "gpu=PASS"; else echo "gpu=DEGRADED"; fi
echo "status=$status"
[ "$status" = PASS ]
