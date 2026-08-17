#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
ROOT_HERMES_HOME="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
ACTIVE_PROFILE="$(cat "$ROOT_HERMES_HOME/active_profile" 2>/dev/null || true)"
ACTIVE_PROFILE="${ACTIVE_PROFILE:-home}"
PROFILE_HOME="$ROOT_HERMES_HOME/profiles/$ACTIVE_PROFILE"
CONFIG_PATH="${HERMES_CONFIG_PATH:-$PROFILE_HOME/config.yaml}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434/v1}"
CLOUD_PROVIDER_KEY="${HERMES_OLLAMA_CLOUD_PROVIDER_KEY:-ollama-cloud}"
LOCAL_PROVIDER_KEY="${HERMES_OLLAMA_LOCAL_PROVIDER_KEY:-ollama-local}"
CLOUD_COMPAT_PROVIDER_KEY="${HERMES_OLLAMA_CLOUD_COMPAT_PROVIDER_KEY:-ollama-cloud-local}"
LOCAL_COMPAT_PROVIDER_KEY="${HERMES_OLLAMA_LOCAL_COMPAT_PROVIDER_KEY:-local-ollama}"
CLOUD_PROVIDER_NAME="${HERMES_OLLAMA_CLOUD_PROVIDER_NAME:-Ollama Cloud}"
LOCAL_PROVIDER_NAME="${HERMES_OLLAMA_LOCAL_PROVIDER_NAME:-Ollama Local}"
CLOUD_COMPAT_PROVIDER_NAME="${HERMES_OLLAMA_CLOUD_COMPAT_PROVIDER_NAME:-Ollama Cloud via local Ollama}"
LOCAL_COMPAT_PROVIDER_NAME="${HERMES_OLLAMA_LOCAL_COMPAT_PROVIDER_NAME:-Windows Ollama}"
LOG_DIR="$ROOT_HERMES_HOME/logs"
LOG_FILE="$LOG_DIR/ollama-model-sync.log"

mkdir -p "$LOG_DIR"

python3 - \
  "$CONFIG_PATH" \
  "$OLLAMA_BASE_URL" \
  "$CLOUD_PROVIDER_KEY" \
  "$LOCAL_PROVIDER_KEY" \
  "$CLOUD_COMPAT_PROVIDER_KEY" \
  "$LOCAL_COMPAT_PROVIDER_KEY" \
  "$CLOUD_PROVIDER_NAME" \
  "$LOCAL_PROVIDER_NAME" \
  "$CLOUD_COMPAT_PROVIDER_NAME" \
  "$LOCAL_COMPAT_PROVIDER_NAME" \
  "$LOG_FILE" <<'PY'
import datetime as _dt
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

import yaml

(
    config_path_s,
    base_url,
    cloud_key,
    local_key,
    cloud_compat_key,
    local_compat_key,
    cloud_name,
    local_name,
    cloud_compat_name,
    local_compat_name,
    log_file_s,
) = sys.argv[1:]

config_path = Path(config_path_s).expanduser()
base_url = base_url.rstrip("/")
log_file = Path(log_file_s).expanduser()

KNOWN_CLOUD_MODELS = {
    "deepseek-v3.1:671b-cloud",
    "deepseek-v4-pro:cloud",
    "devstral-2:123b-cloud",
    "devstral-small-2:24b-cloud",
    "gemma4:31b-cloud",
    "glm-5.1:cloud",
    "glm-5.2:cloud",
    "gpt-oss:120b-cloud",
    "gpt-oss:20b-cloud",
    "kimi-k2.6:cloud",
    "ministral-3:14b-cloud",
    "nemotron-3-nano:30b-cloud",
    "qwen3-coder:480b-cloud",
    "qwen3-next:80b-cloud",
    "qwen3-vl:235b-cloud",
    "qwen3.5:cloud",
}

OLLAMA_PROVIDER_KEYS = {
    "ollama",
    "ollama-cloud",
    "ollama-local",
    "ollama-cloud-local",
    "local-ollama",
}


def log(message: str) -> None:
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def fetch_model_ids() -> list[str]:
    url = f"{base_url}/models"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer ollama"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ids = []
    for item in payload.get("data", []):
        model_id = str(item.get("id") or "").strip()
        if model_id:
            ids.append(model_id)
    return sorted(dict.fromkeys(ids), key=str.lower)


def is_cloud_model(model_id: str) -> bool:
    tag = str(model_id or "").rsplit(":", 1)[-1].strip().lower()
    return tag == "cloud" or tag.endswith("-cloud")


def model_list(raw) -> list[str]:
    if isinstance(raw, dict):
        return [str(key).strip() for key in raw.keys() if str(key).strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]
    return []


def collect_existing_models(config: dict) -> set[str]:
    found: set[str] = set()
    model = config.get("model")
    if isinstance(model, dict):
        provider = str(model.get("provider") or "").strip()
        for key in ("default", "model"):
            value = str(model.get(key) or "").strip()
            if value and provider in OLLAMA_PROVIDER_KEYS:
                found.add(value)
    elif isinstance(model, str) and model.strip():
        provider = str(config.get("provider") or "").strip()
        if provider in OLLAMA_PROVIDER_KEYS:
            found.add(model.strip())

    fallback = config.get("fallback_providers") or config.get("fallback_model") or []
    if isinstance(fallback, dict):
        fallback = [fallback]
    if isinstance(fallback, list):
        for entry in fallback:
            if isinstance(entry, dict):
                provider = str(entry.get("provider") or "").strip()
                value = str(entry.get("model") or "").strip()
                if value and provider in OLLAMA_PROVIDER_KEYS:
                    found.add(value)

    providers = config.get("providers")
    if isinstance(providers, dict):
        for entry in providers.values():
            if isinstance(entry, dict):
                value = str(entry.get("default_model") or entry.get("model") or "").strip()
                if value:
                    found.add(value)
                found.update(model_list(entry.get("models")))

    custom_providers = config.get("custom_providers")
    if isinstance(custom_providers, list):
        for entry in custom_providers:
            if isinstance(entry, dict):
                value = str(entry.get("model") or "").strip()
                if value:
                    found.add(value)
                found.update(model_list(entry.get("models")))

    return found


def choose_default(models: list[str], preferred: list[str], existing: str = "") -> str:
    candidates = []
    if existing:
        candidates.append(existing)
    candidates.extend(preferred)
    for candidate in candidates:
        if candidate in models:
            return candidate
    return models[0] if models else ""


def provider_existing_default(config: dict, key: str) -> str:
    providers = config.get("providers")
    if isinstance(providers, dict):
        entry = providers.get(key)
        if isinstance(entry, dict):
            value = str(entry.get("default_model") or "").strip()
            if value:
                return value
    return ""


def ensure_provider(config: dict, key: str, name: str, default_model: str, models: list[str]) -> None:
    providers = config.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        config["providers"] = providers
    entry = providers.setdefault(key, {})
    if not isinstance(entry, dict):
        entry = {}
        providers[key] = entry
    entry["api"] = base_url
    entry["name"] = name
    if default_model:
        entry["default_model"] = default_model
    entry["api_key"] = "ollama"
    entry["api_mode"] = "chat_completions"
    entry["models"] = {model_id: {} for model_id in models}


def ensure_custom_provider(config: dict, key: str, default_model: str, models: list[str]) -> None:
    custom_providers = config.setdefault("custom_providers", [])
    if not isinstance(custom_providers, list):
        custom_providers = []
        config["custom_providers"] = custom_providers

    entry = None
    for item in custom_providers:
        if isinstance(item, dict) and str(item.get("name") or "").strip() == key:
            entry = item
            break
    if entry is None:
        entry = {"name": key}
        custom_providers.append(entry)

    entry["base_url"] = base_url
    entry["api_key"] = "ollama"
    entry["api_mode"] = "chat_completions"
    if default_model:
        entry["model"] = default_model
    entry["models"] = models


if not config_path.exists():
    raise SystemExit(f"Hermes config not found: {config_path}")

try:
    fetched_model_ids = fetch_model_ids()
except Exception as exc:
    log(f"SKIP error=failed_to_fetch_ollama_models detail={exc}")
    raise SystemExit(0)

config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
if not isinstance(config, dict):
    raise SystemExit(f"Hermes config root is not a mapping: {config_path}")

model_ids = set(fetched_model_ids)
model_ids.update(collect_existing_models(config))
model_ids.update(KNOWN_CLOUD_MODELS)

extra_cloud = os.getenv("HERMES_OLLAMA_CLOUD_MODELS_EXTRA", "")
extra_local = os.getenv("HERMES_OLLAMA_LOCAL_MODELS_EXTRA", "")
model_ids.update(item.strip() for item in extra_cloud.split(",") if item.strip())
model_ids.update(item.strip() for item in extra_local.split(",") if item.strip())

cloud_models = sorted({model_id for model_id in model_ids if is_cloud_model(model_id)}, key=str.lower)
local_models = sorted({model_id for model_id in model_ids if model_id and not is_cloud_model(model_id)}, key=str.lower)

if not cloud_models and not local_models:
    log("SKIP error=no_models_from_ollama_or_config")
    raise SystemExit(0)

model_cfg = config.get("model") if isinstance(config.get("model"), dict) else {}
current_default = str(model_cfg.get("default") or model_cfg.get("model") or "").strip()
current_cloud_default = current_default if is_cloud_model(current_default) else ""
current_local_default = current_default if current_default and not is_cloud_model(current_default) else ""

cloud_default = choose_default(
    cloud_models,
    [
        os.getenv("HERMES_OLLAMA_CLOUD_DEFAULT", "").strip(),
        current_cloud_default,
        provider_existing_default(config, cloud_key),
        provider_existing_default(config, cloud_compat_key),
        "glm-5.2:cloud",
        "gemma4:31b-cloud",
    ],
)
local_default = choose_default(
    local_models,
    [
        os.getenv("HERMES_OLLAMA_LOCAL_DEFAULT", "").strip(),
        current_local_default,
        provider_existing_default(config, local_key),
        provider_existing_default(config, local_compat_key),
        "hermes3:8b",
    ],
)

ensure_provider(config, cloud_key, cloud_name, cloud_default, cloud_models)
ensure_provider(config, local_key, local_name, local_default, local_models)
ensure_provider(config, cloud_compat_key, cloud_compat_name, cloud_default, cloud_models)
ensure_provider(config, local_compat_key, local_compat_name, local_default, local_models)

ensure_custom_provider(config, cloud_key, cloud_default, cloud_models)
ensure_custom_provider(config, local_key, local_default, local_models)
ensure_custom_provider(config, cloud_compat_key, cloud_default, cloud_models)
ensure_custom_provider(config, local_compat_key, local_default, local_models)

backup_path = config_path.with_suffix(config_path.suffix + f".bak-{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}")
shutil.copy2(config_path, backup_path)
config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")

log(
    "OK "
    f"cloud_provider={cloud_key} cloud_models={len(cloud_models)} "
    f"local_provider={local_key} local_models={len(local_models)} "
    f"config={config_path} backup={backup_path}"
)
print(json.dumps({
    "cloud_provider": cloud_key,
    "cloud_default": cloud_default,
    "cloud_models": len(cloud_models),
    "local_provider": local_key,
    "local_default": local_default,
    "local_models": len(local_models),
}, indent=2))
PY
