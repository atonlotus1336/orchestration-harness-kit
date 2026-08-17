#!/usr/bin/env bash
set -euo pipefail

PROFILE_NAME="${1:-}"
MODEL_NAME="${2:-}"
PROVIDER_NAME="${3:-}"

if [ -z "$PROFILE_NAME" ]; then
    echo "Usage: apply-hermes-profile-to-wsl.sh <profile> <model> [provider]" >&2
    exit 2
fi

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"
export PATH="$USER_HOME/.local/bin:$PATH"

ROOT_HERMES_HOME="${HERMES_ROOT_HOME:-$USER_HOME/.hermes}"
HERMES_BIN="${HERMES_BIN:-$ROOT_HERMES_HOME/hermes-agent/venv/bin/hermes}"
HERMES_PYTHON="${HERMES_PYTHON:-$(dirname "$HERMES_BIN")/python3}"
SOURCE_AGENT_ROOT="${HERMES_SOURCE_AGENT_ROOT:-__WSL_WORKSPACE__/.hermes/hermes-agent}"
__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE="${__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE:-__WSL_WORKSPACE__/hermes-custom-plugins/__PRIVATE_TOKEN_02__-parity}"
LOG_DIR="$ROOT_HERMES_HOME/logs"
mkdir -p "$LOG_DIR"

sync_runtime_code() {
    # Runtime source synchronization is opt-in.  Copying a workspace mirror
    # over a git-managed upstream runtime caused a stale 1,000-file overlay on
    # NUCBOX and made safe upstream updates impossible.
    if [ "${HERMES_ENABLE_RUNTIME_CODE_SYNC:-}" != "1" ]; then
        return 0
    fi
    if [ ! -d "$SOURCE_AGENT_ROOT" ]; then
        echo "Hermes source agent root not found: $SOURCE_AGENT_ROOT" >&2
        return 1
    fi
    local runtime_root="$ROOT_HERMES_HOME/hermes-agent"
    if [ ! -d "$runtime_root" ]; then
        echo "Hermes runtime agent root not found: $runtime_root" >&2
        return 1
    fi

    local marker="$runtime_root/.last_runtime_sync_source"
    local source_sig
    source_sig="$(
        cd "$SOURCE_AGENT_ROOT"
        find agent gateway hermes_cli cron providers tools plugins acp_adapter acp_registry tui_gateway -type f -printf '%P %s %T@\n' 2>/dev/null
        find . -maxdepth 1 -type f -name '*.py' -printf '%P %s %T@\n' 2>/dev/null
    )"
    if [ -f "$marker" ] && [ "$(cat "$marker")" = "$source_sig" ]; then
        return 0
    fi

    local backup="$ROOT_HERMES_HOME/hermes-agent.backup-runtime-sync-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$backup"
    for path in agent gateway hermes_cli cron providers tools plugins acp_adapter acp_registry tui_gateway; do
        if [ ! -e "$SOURCE_AGENT_ROOT/$path" ]; then
            continue
        fi
        if [ -e "$runtime_root/$path" ]; then
            cp -a "$runtime_root/$path" "$backup/$path"
        fi
        # Copy directory contents into the existing package directory.  Copying
        # the directory itself would create a nested "$path/$path" tree and
        # leave the runtime importing the old top-level modules.
        mkdir -p "$runtime_root/$path"
        cp -a "$SOURCE_AGENT_ROOT/$path/." "$runtime_root/$path/"
    done
    cp -a "$SOURCE_AGENT_ROOT"/*.py "$runtime_root/"
    printf '%s' "$source_sig" > "$marker"
    echo "synced Hermes runtime code from $SOURCE_AGENT_ROOT (backup: $backup)"
}

sync___PRIVATE_TOKEN_02___parity_plugin() {
    if [ ! -f "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE/plugin.yaml" ] || [ ! -f "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE/__init__.py" ]; then
        echo "WARNING: __PRIVATE_TOKEN_02__-parity plugin source missing at $__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE; skipping its sync" >&2
        return 0
    fi
    local profile_home="$1"
    mkdir -p "$profile_home/plugins"
    rm -rf "$profile_home/plugins/__PRIVATE_TOKEN_02__-parity"
    ln -sfn "$__PRIVATE_TOKEN_02___PARITY_PLUGIN_SOURCE" "$profile_home/plugins/__PRIVATE_TOKEN_02__-parity"
}

preflight_runtime_imports() {
    if [ "${HERMES_SKIP_RUNTIME_PREFLIGHT:-}" = "1" ]; then
        return 0
    fi
    if [ ! -x "$HERMES_PYTHON" ]; then
        echo "Hermes Python not found or not executable: $HERMES_PYTHON" >&2
        return 1
    fi
    HERMES_HOME="$1" "$HERMES_PYTHON" - <<'PY'
from cron.scheduler import _resolve_home_env_var
from gateway.run import start_gateway
from tui_gateway import server as tui_gateway_server

assert callable(_resolve_home_env_var)
assert callable(start_gateway)
assert tui_gateway_server is not None
print("runtime import preflight OK")
PY
}

python3 - "$ROOT_HERMES_HOME" "$PROFILE_NAME" "$MODEL_NAME" "$PROVIDER_NAME" <<'PY'
from pathlib import Path
import hashlib
import difflib
import json
import os
import re
import sys

root = Path(sys.argv[1])
profile = sys.argv[2].strip()
model = sys.argv[3].strip()
provider = sys.argv[4].strip()

ORCHESTRATION_KEYS = (
    "providers",
    "mcp_servers",
    "memory",
    "toolsets",
)

# Every server a rendered profile must end up with. This script overlays whole
# top-level blocks from the WSL root config, so a root config that has drifted
# (or a source profile that predates a server) silently hands back a Hermes with
# fewer tools -- which reads as "working" everywhere: the gateway still answers
# /health, the MCP block still exists, the lint above still passes.
# Asserted BEFORE the render is written, so a bad render never reaches disk.
CANONICAL_MCP_SERVERS = (
    "obsidian",
    "rag-orchestrator",
    "memoryweaver-mcp",
    "context-mode",
    "wordpress",
    "n8n",
    "__PRIVATE_TOKEN_02__-control-plane",
)

# 10.0.0.1 was the WSL2 NAT host address. .wslconfig now uses
# networkingMode=mirrored, so this address answers nothing. rag-orchestrator
# connects fine with it configured and every RAG query then fails at the
# backend, so it must never be reintroduced by a render.
DEAD_BACKEND_HOSTS = ("10.0.0.1",)

GLOBAL_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OLLAMA_API_KEY",
    "OLLAMA_BASE_URL",
    "OPENROUTER_API_KEY",
    "API_SERVER_ENABLED",
    "API_SERVER_HOST",
    "API_SERVER_PORT",
    "API_SERVER_KEY",
    "API_SERVER_MODEL_NAME",
    "COMFYUI_WINDOWS_ROOT",
    "COMFYUI_WINDOWS_BATCH",
    "COMFYUI_HOST",
    "COMFYUI_PORT",
    "COMFYUI_DEFAULT_CHECKPOINT",
    "COMFYUI_OUTPUT_DIR",
    "COMFYUI_WORKFLOW_DIR",
    "COMFYUI_START_WAIT_SECONDS",
    "COMFYUI_BRIDGE_STATE_PATH",
    "COMFYUI_BRIDGE_LOG_PATH",
    "COMFYUI_CONTRACT_PATH",
    "COMFYUI_API_RETRY_ATTEMPTS",
    "COMFYUI_API_RETRY_DELAY_SECONDS",
    "COMFYUI_OUTPUT_SETTLE_TIMEOUT_SECONDS",
    "COMFYUI_WATCH_DIR",
    "COMFYUI_WATCH_EXTENSIONS",
    "COMFYUI_WATCH_MIN_BYTES",
    "COMFYUI_WATCH_SETTLE_SECONDS",
    "COMFYUI_WATCH_POLL_SECONDS",
)


def top_level_block(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\n(?:^(?:[ \t].*|-\s.*)\n|^\n)*", text, re.M)
    return match.group(0).rstrip() + "\n" if match else ""


def replace_top_level_block(text: str, key: str, block: str) -> str:
    if not block:
        return text
    # Consume accidental unindented list entries immediately following the
    # block too.  A prior malformed render left ``- hermes-cli`` after the
    # ``toolsets`` mapping, which the old matcher preserved on every pass.
    pattern = rf"^{re.escape(key)}:\n(?:^(?:[ \t].*|-\s.*)\n|^\n)*"
    if re.search(pattern, text, re.M):
        return re.sub(pattern, lambda _match: block, text, count=1, flags=re.M)
    separator = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{separator}{block}"


def canonical_telegram_route() -> tuple[str, str]:
    path = managed_profiles_root.parent / "docs" / "telegram-default-model.yaml"
    if not path.exists():
        return provider, model
    raw = path.read_text(encoding="utf-8", errors="ignore")

    def value(key: str) -> str:
        match = re.search(rf"^\s+{re.escape(key)}:\s*(.+?)\s*$", raw, re.M)
        return match.group(1).strip().strip("\"'") if match else ""

    return value("provider") or provider, value("model") or model


def set_top_level_model(text: str, provider_name: str, model_name: str) -> str:
    if not provider_name or not model_name:
        return text
    block = f"model:\n  provider: {provider_name}\n  default: {model_name}\n"
    if re.match(r"^model:\n(?:  .*\n)*", text):
        return re.sub(r"^model:\n(?:  .*\n)*", block, text, count=1)
    return block + text


def set_provider_default_model(text: str, provider_name: str, model_name: str) -> str:
    lines = text.splitlines()
    in_providers = False
    in_provider = False
    insert_at: int | None = None

    for index, line in enumerate(lines):
        if line == "providers:":
            in_providers = True
            continue
        if not in_providers:
            continue
        if line and not line.startswith(" "):
            break
        if line == f"  {provider_name}:":
            in_provider = True
            insert_at = index + 1
            continue
        if not in_provider:
            continue
        if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            lines.insert(index, f"    default_model: {model_name}")
            return "\n".join(lines) + "\n"
        if line.startswith("    default_model:"):
            lines[index] = f"    default_model: {model_name}"
            return "\n".join(lines) + "\n"
        if line.startswith("    "):
            insert_at = index + 1

    if in_provider and insert_at is not None:
        lines.insert(insert_at, f"    default_model: {model_name}")
        return "\n".join(lines) + "\n"
    return text


def set_custom_provider_model(text: str, provider_name: str, model_name: str) -> str:
    lines = text.splitlines()
    in_custom = False
    in_item = False
    insert_at: int | None = None

    for index, line in enumerate(lines):
        if line == "custom_providers:":
            in_custom = True
            continue
        if not in_custom:
            continue
        if line and not line.startswith(" ") and not line.startswith("- "):
            break
        if line == f"- name: {provider_name}":
            in_item = True
            insert_at = index + 1
            continue
        if not in_item:
            continue
        if line.startswith("- name:"):
            lines.insert(index, f"  model: {model_name}")
            return "\n".join(lines) + "\n"
        if line.startswith("  model:"):
            lines[index] = f"  model: {model_name}"
            return "\n".join(lines) + "\n"
        if line.startswith("  "):
            insert_at = index + 1

    if in_item and insert_at is not None:
        lines.insert(insert_at, f"  model: {model_name}")
        return "\n".join(lines) + "\n"
    return text


def is_ollama_cloud_model(model_name: str) -> bool:
    tag = (model_name or "").rsplit(":", 1)[-1].strip().lower()
    return tag == "cloud" or tag.endswith("-cloud")


def default_ollama_provider_for_model(model_name: str) -> str:
    return "ollama-cloud" if is_ollama_cloud_model(model_name) else "ollama-local"


def force_telegram_canonical_route(text: str) -> tuple[str, str, str]:
    provider_name, model_name = canonical_telegram_route()
    text = set_top_level_model(text, provider_name, model_name)
    text = set_provider_default_model(text, provider_name, model_name)
    text = set_custom_provider_model(text, provider_name, model_name)
    return text, provider_name, model_name


def ensure_context_mode_mcp(text: str) -> str:
    if re.search(r"(?m)^  context-mode:\s*$", text):
        return text
    block = (
        "  context-mode:\n"
        "    command: python3\n"
        "    args:\n"
        "    - __WSL_WORKSPACE__/.mcp-servers/context-mode/server.py\n"
    )
    match = re.search(r"(?m)^mcp_servers:\s*$", text)
    if not match:
        return text.rstrip() + "\nmcp_servers:\n" + block
    next_top = re.search(r"(?m)^[A-Za-z0-9_-]+:\s*$", text[match.end():])
    if not next_top:
        return text.rstrip() + "\n" + block
    insert_at = match.end() + next_top.start()
    return text[:insert_at] + block + text[insert_at:]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value
    return values


def sync_global_env(root_env: Path, target_env: Path) -> None:
    src_values = load_env(root_env)
    desired = {key: src_values[key] for key in GLOBAL_ENV_KEYS if key in src_values and src_values[key] != ""}
    if not desired:
        return

    lines = target_env.read_text(encoding="utf-8", errors="ignore").splitlines() if target_env.exists() else []
    out: list[str] = []
    seen: set[str] = set()
    changed = False

    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in desired:
            new_line = f"{key}={desired[key]}"
            out.append(new_line)
            seen.add(key)
            if line != new_line:
                changed = True
        else:
            out.append(line)

    for key, value in desired.items():
        if key not in seen:
            out.append(f"{key}={value}")
            changed = True

    if changed or not target_env.exists():
        target_env.write_text("\n".join(out) + "\n", encoding="utf-8")
        try:
            target_env.chmod(0o600)
        except Exception:
            pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_secret_scalars(text: str) -> list[str]:
    findings: list[str] = []
    secret_key = re.compile(r"(api[_-]?key|token|secret|password|credential)", re.I)
    env_ref = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = value.strip().strip("\"'")
        if not value or value in {"[]", "{}"} or env_ref.match(value):
            continue
        key_name = key.strip()
        if re.search(r"(_URL|_URI|_HOST|_PORT|_PATH|_DIR|_FILE)$", key_name, re.I):
            continue
        if secret_key.search(key_name) and value not in {"ollama", "none", "null", "false", "true"}:
            findings.append(f"line {lineno}: {key_name}")
    return findings


def assert_orchestration_intact(text: str, label: str) -> None:
    """Refuse to write a render that would quietly downgrade the profile.

    Runs on the in-memory text BEFORE the file is written: a post-write check
    would leave the broken config on disk after raising.
    """
    problems = []

    missing = [
        name for name in CANONICAL_MCP_SERVERS
        if not re.search(rf"(?m)^  {re.escape(name)}:\s*$", text)
    ]
    if missing:
        problems.append(
            f"render is missing {len(missing)}/{len(CANONICAL_MCP_SERVERS)} MCP servers: "
            + ", ".join(missing)
        )

    dead = [host for host in DEAD_BACKEND_HOSTS if host in text]
    if dead:
        problems.append(f"render reintroduces dead backend host(s): {', '.join(dead)}")

    if problems:
        raise SystemExit(
            f"REFUSING to write {label}: " + "; ".join(problems)
            + ".\nThe source profile or the WSL root config has drifted. Fix the source "
            "instead of letting this overwrite a working profile -- verify with "
            "bash __WSL_WORKSPACE__/scripts/check-hermes-mcp.sh"
        )


def lint_rendered_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    missing = [key for key in ORCHESTRATION_KEYS if not re.search(rf"^{re.escape(key)}:\s*$", text, re.M)]
    secrets = scan_secret_scalars(text)
    __PRIVATE_TOKEN_02___count = len(re.findall(r"\b__PRIVATE_TOKEN_02__-control-plane\b", text))
    errors = []
    if missing:
        errors.append(f"missing required blocks: {', '.join(missing)}")
    if secrets:
        errors.append(f"literal secret-like scalars: {', '.join(secrets)}")
    if errors:
        raise SystemExit(f"post-compile lint failed for {path}: {'; '.join(errors)}")
    return {
        "target": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "required_blocks": {key: True for key in ORCHESTRATION_KEYS},
        "secret_scan_findings": [],
        "__PRIVATE_TOKEN_02___control_plane_count": __PRIVATE_TOKEN_02___count,
    }


if not profile:
    raise SystemExit("profile is required")

runtime_profile = "home" if profile == "default" else profile.lower()

if not provider or provider == "ollama":
    provider = default_ollama_provider_for_model(model)

managed_profiles_root = Path("__WSL_WORKSPACE__/hermes-agent-self-evolution/profiles")
managed_source = managed_profiles_root / runtime_profile / "config.yaml"
windows_profiles_root = Path(os.environ.get("HERMES_WINDOWS_ROOT", "/mnt/c/Users/__WINDOWS_USER__/.hermes")) / "profiles"
windows_source = windows_profiles_root / runtime_profile / "config.yaml"
if managed_source.exists():
    source = managed_source
elif windows_source.exists():
    source = windows_source
else:
    existing_profile_source = root / "profiles" / runtime_profile / "config.yaml"
    if existing_profile_source.exists():
        source = existing_profile_source
    else:
        candidates = {"default", "home"}
        for profiles_root in (managed_profiles_root, root / "profiles"):
            if profiles_root.exists():
                for child in profiles_root.iterdir():
                    if child.is_dir() and (child / "config.yaml").exists():
                        candidates.add(child.name)
        matches = difflib.get_close_matches(runtime_profile, sorted(candidates), n=5, cutoff=0)
        hint = f" Available profiles: {', '.join(matches)}." if matches else ""
        print(
            f"I couldn't find a Hermes profile named '{profile}', so I did not change the active profile. "
            f"Which profile did you mean?{hint}",
            file=sys.stderr,
        )
        raise SystemExit(3)

target_dir = root / "profiles" / runtime_profile
target_dir.mkdir(parents=True, exist_ok=True)
target = target_dir / "config.yaml"

text = source.read_text(errors="ignore") if source.exists() else ""
root_text = (root / "config.yaml").read_text(errors="ignore") if (root / "config.yaml").exists() else ""

if model:
    text = set_top_level_model(text, provider, model)

for key in ORCHESTRATION_KEYS:
    text = replace_top_level_block(text, key, top_level_block(root_text, key))

text = ensure_context_mode_mcp(text)

if runtime_profile == "telegram":
    text, provider, model = force_telegram_canonical_route(text)

assert_orchestration_intact(text, str(target))

target.write_text(text)
sync_global_env(root / ".env", target_dir / ".env")
(root / "active_profile").write_text(runtime_profile + "\n")
manifest = {
    "schema": "hermes-profile-render-manifest/1",
    "profile": runtime_profile,
    "source": str(source),
    "target": lint_rendered_config(target),
}
(target_dir / "render-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"applied {runtime_profile}: {provider}/{model or 'existing-model'}")
PY

RUNTIME_PROFILE="$([ "$PROFILE_NAME" = "default" ] && echo home || echo "$PROFILE_NAME" | tr '[:upper:]' '[:lower:]')"
TARGET_HERMES_HOME="$ROOT_HERMES_HOME/profiles/$RUNTIME_PROFILE"
PROFILE_CONFIG="$TARGET_HERMES_HOME/config.yaml"
PROFILE_BACKUP="$TARGET_HERMES_HOME/config.yaml.prime-activation.bak"
cp "$PROFILE_CONFIG" "$PROFILE_BACKUP"
PYTHONPATH="__WSL_WORKSPACE__/hermes-agent-self-evolution${PYTHONPATH:+:$PYTHONPATH}" \
    python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/render-prime-profile-overlay.py \
    --profiles-root "$ROOT_HERMES_HOME/profiles" \
    --profile "$RUNTIME_PROFILE" \
    --policy __WSL_WORKSPACE__/hermes-agent-self-evolution/config/prime-profile-policy.json \
    --ledger "$ROOT_HERMES_HOME/state/prime/jobs.sqlite3" \
    --in-place
PROFILE_MODE="$(python3 - "$RUNTIME_PROFILE" __WSL_WORKSPACE__/hermes-agent-self-evolution/config/prime-profile-policy.json <<'PY'
import json, sys
profile, path = sys.argv[1:]
policy = json.load(open(path, encoding="utf-8"))
print(policy["profile_modes"].get(profile, policy["default_mode"]))
PY
)"
if ! python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/verify-prime-profile-readiness.py \
    --config "$PROFILE_CONFIG" \
    --receipt "$TARGET_HERMES_HOME/prime-overlay-receipt.json" \
    --profile "$RUNTIME_PROFILE" \
    --mode "$PROFILE_MODE"; then
    mv "$PROFILE_BACKUP" "$PROFILE_CONFIG"
    echo "Prime overlay readiness verification failed; restored prior profile config." >&2
    exit 1
fi
rm -f "$PROFILE_BACKUP"
sync___PRIVATE_TOKEN_02___parity_plugin "$TARGET_HERMES_HOME"

if [ "${HERMES_PROFILE_SYNC_NO_RESTART:-}" = "1" ]; then
    exit 0
fi

if [ ! -x "$HERMES_BIN" ]; then
    echo "Hermes binary not found or not executable: $HERMES_BIN" >&2
    exit 1
fi

if [ "$RUNTIME_PROFILE" = "telegram" ]; then
    python3 - "$TARGET_HERMES_HOME/.env" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
updates = {
    "API_SERVER_ENABLED": "true",
    "API_SERVER_HOST": "127.0.0.1",
    "API_SERVER_PORT": "8643",
}
lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines() if path.exists() else []
out = []
seen = set()
for line in lines:
    if "=" not in line or line.lstrip().startswith("#"):
        out.append(line)
        continue
    key, _value = line.split("=", 1)
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
else
    # The NUCBOX gateway is the shared backend for the __OWNER_HANDLE__ thin client.
    # A profile-local .env overrides config.yaml, so enforce the tailnet-safe
    # bind here after every profile render instead of relying on stale root env.
    python3 - "$TARGET_HERMES_HOME/.env" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {"API_SERVER_HOST": "0.0.0.0", "API_SERVER_PORT": "8642"}
lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines() if path.exists() else []
out, seen = [], set()
for line in lines:
    if "=" not in line or line.lstrip().startswith("#"):
        out.append(line)
        continue
    key, _value = line.split("=", 1)
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
fi

sync_runtime_code
preflight_runtime_imports "$TARGET_HERMES_HOME"

if [ -f "$TARGET_HERMES_HOME/.env" ]; then
    eval "$(
        python3 - "$TARGET_HERMES_HOME/.env" <<'PY'
from pathlib import Path
import re
import shlex
import sys

path = Path(sys.argv[1])
for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        continue
    print(f"export {key}={shlex.quote(value)}")
PY
    )"
fi

# WSL does not automatically export its distribution name to child processes.
# Prime's containment admission uses this value to refuse execution outside the
# approved NUCBOX distribution, so preserve the actual supported target across
# the gateway -> MCP -> Prime worker process tree.
export WSL_DISTRO_NAME="${WSL_DISTRO_NAME:-Ubuntu-restored}"

PYTHONPATH="__WSL_WORKSPACE__/hermes-agent-self-evolution${PYTHONPATH:+:$PYTHONPATH}" \
    nohup "$HERMES_BIN" --profile "$RUNTIME_PROFILE" gateway run --replace >> "$LOG_DIR/gateway.log" 2>&1 &

HEALTH_PORT="$(
    python3 - "$TARGET_HERMES_HOME/.env" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
port = "8642"
if path.exists():
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("API_SERVER_PORT="):
            port = line.split("=", 1)[1].strip() or port
print(port)
PY
)"

for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf "http://127.0.0.1:${HEALTH_PORT}/health" >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done

echo "Hermes gateway did not become reachable on http://127.0.0.1:${HEALTH_PORT}" >&2
exit 1
