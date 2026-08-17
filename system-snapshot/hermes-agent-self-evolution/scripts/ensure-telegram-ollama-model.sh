#!/usr/bin/env bash
set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
BASE_MODEL="${TELEGRAM_GEMMA_BASE_MODEL:-gemma4-ollamauncensored:latest}"
ALIAS_MODEL="${TELEGRAM_GEMMA_ALIAS_MODEL:-gemma4-ollamauncensored-32k:latest}"
NUM_CTX="${TELEGRAM_GEMMA_NUM_CTX:-32768}"
SMOKE=0
REQUIRED=0

for arg in "$@"; do
  case "$arg" in
    --smoke) SMOKE=1 ;;
    --required) REQUIRED=1 ;;
    --warn-only) REQUIRED=0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

python3 - "$OLLAMA_HOST" "$BASE_MODEL" "$ALIAS_MODEL" "$NUM_CTX" "$SMOKE" "$REQUIRED" <<'PY'
import json
import sys
import urllib.error
import urllib.request

host, base_model, alias_model, num_ctx_s, smoke_s, required_s = sys.argv[1:]
num_ctx = int(num_ctx_s)
smoke = smoke_s == "1"
required = required_s == "1"


def request(path: str, payload: dict | None = None, timeout: int = 45) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{host.rstrip('/')}{path}", data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def fail(message: str, code: int = 1) -> None:
    prefix = "ERROR" if required else "WARNING"
    print(f"{prefix}: {message}", file=sys.stderr)
    raise SystemExit(code if required else 0)


try:
    tags = request("/api/tags", timeout=8)
except Exception as exc:
    fail(f"Ollama is not reachable at {host}: {exc}")

available = {item.get("name") for item in tags.get("models", []) if isinstance(item, dict)}
if base_model not in available:
    fail(f"Base model is missing from Ollama: {base_model}")

payload = {
    "model": alias_model,
    "from": base_model,
    "system": "You are a local assistant. Follow the user's instructions directly and answer concisely.",
    "parameters": {
        "temperature": 0.2,
        "num_ctx": num_ctx,
        "stop": ["<turn|>"],
    },
    "stream": False,
}

try:
    request("/api/create", payload, timeout=120)
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    fail(f"Failed to create {alias_model} from {base_model}: HTTP {exc.code} {body}")
except Exception as exc:
    fail(f"Failed to create {alias_model} from {base_model}: {exc}")

if smoke:
    chat = {
        "model": alias_model,
        "stream": False,
        "messages": [{"role": "user", "content": "Reply exactly: alias-ok"}],
    }
    try:
        result = request("/v1/chat/completions", chat, timeout=160)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        fail(f"Alias smoke test failed for {alias_model}: HTTP {exc.code} {body}")
    except Exception as exc:
        fail(f"Alias smoke test failed for {alias_model}: {exc}")
    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if content != "alias-ok":
        fail(f"Alias smoke test returned unexpected content: {content!r}")

print(json.dumps({
    "status": "ok",
    "host": host,
    "base_model": base_model,
    "alias_model": alias_model,
    "num_ctx": num_ctx,
    "smoke": smoke,
}, indent=2))
PY
