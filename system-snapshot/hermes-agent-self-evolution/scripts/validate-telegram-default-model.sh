#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"

python3 - <<'PY'
from pathlib import Path
import json
import re

ROOT = Path("__WSL_WORKSPACE__")
SELF = ROOT / "hermes-agent-self-evolution"
CANONICAL = SELF / "docs" / "telegram-default-model.yaml"
SOURCE_CONFIG = SELF / "profiles" / "telegram" / "config.yaml"
WSL_CONFIG = Path.home() / ".hermes" / "profiles" / "telegram" / "config.yaml"
WINDOWS_CONFIG = Path("/mnt/c/Users/__WINDOWS_USER__/.hermes/profiles/telegram/config.yaml")

DOCS_TO_CHECK = [
    SELF / "profiles" / "telegram" / "README.md",
    SELF / "docs" / "model-profile-switching-contract.md",
    ROOT / "hermes-webui/hermes-workspace/docs/telegram-comfyui-setup.md",
    ROOT / "hermes-webui/hermes-workspace/docs/troubleshooting.md",
    ROOT / "hermes-webui/hermes-workspace/docs/LLM-TROUBLESHOOTING-ROUTER.md",
]


def canonical_value(key: str) -> str:
    text = CANONICAL.read_text(encoding="utf-8")
    match = re.search(rf"^\s+{re.escape(key)}:\s*(.+?)\s*$", text, re.M)
    if not match:
        raise SystemExit(f"Missing canonical key: {key}")
    return match.group(1).strip().strip("\"'")


provider = canonical_value("provider")
model = canonical_value("model")
profile = canonical_value("profile")
gateway_port = canonical_value("gateway_port")
apply_command = canonical_value("apply_command")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def model_block_ok(text: str) -> bool:
    return bool(
        re.search(
            rf"^model:\s*\n\s+provider:\s*{re.escape(provider)}\s*\n\s+default:\s*{re.escape(model)}\s*$",
            text,
            re.M,
        )
    )


checks: dict[str, bool] = {
    "canonical_exists": CANONICAL.exists(),
    "canonical_profile_telegram": profile == "telegram",
    "canonical_gateway_port_8643": gateway_port == "8643",
    "canonical_apply_command_matches": apply_command.endswith(
        f" telegram {model} {provider}"
    ),
}

for label, path in {
    "source": SOURCE_CONFIG,
    "wsl": WSL_CONFIG,
    "windows": WINDOWS_CONFIG,
}.items():
    text = read(path)
    checks[f"{label}_config_exists"] = bool(text)
    checks[f"{label}_model_block_matches_canonical"] = model_block_ok(text)
    checks[f"{label}_provider_default_matches_canonical"] = (
        f"default_model: {model}" in text
    )
    checks[f"{label}_custom_provider_model_matches_canonical"] = (
        f"model: {model}" in text
    )

for path in DOCS_TO_CHECK:
    text = read(path)
    key = str(path.relative_to(ROOT))
    checks[f"doc_mentions_canonical:{key}"] = "telegram-default-model.yaml" in text
    checks[f"doc_redirects_to_canonical_or_mentions_model:{key}"] = (
        "telegram-default-model.yaml" in text or model in text
    )

status = "ok" if all(checks.values()) else "error"
print(
    json.dumps(
        {
            "status": status,
            "canonical": {
                "profile": profile,
                "provider": provider,
                "model": model,
                "gateway_port": gateway_port,
            },
            "checks": checks,
            "change_protocol": [
                "Edit __WSL_WORKSPACE__/hermes-agent-self-evolution/docs/telegram-default-model.yaml first.",
                "Update __WSL_WORKSPACE__/hermes-agent-self-evolution/profiles/telegram/config.yaml to match.",
                "Run sync-g-managed-telegram-profile.sh.",
                "Run apply-hermes-profile-to-wsl.sh telegram <model> <provider>.",
                "Run this validator again.",
            ],
        },
        indent=2,
    )
)
raise SystemExit(0 if status == "ok" else 1)
PY
