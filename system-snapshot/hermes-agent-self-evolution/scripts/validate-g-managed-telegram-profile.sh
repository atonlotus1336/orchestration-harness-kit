#!/usr/bin/env bash
set -euo pipefail

USER_HOME="${HERMES_USER_HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export HOME="$USER_HOME"

python3 - <<'PY'
from pathlib import Path
import json
import os

G_ROOT = Path('__WSL_WORKSPACE__/hermes-agent-self-evolution')
WSL_ROOT = Path.home() / '.hermes'
WIN_ROOT = Path('/mnt/c/Users/__WINDOWS_USER__/.hermes')
PROFILE_SOURCE = G_ROOT / 'profiles' / 'telegram'
PLUGIN_SOURCE = Path('__WSL_WORKSPACE__/hermes-custom-plugins/image_gen/comfyui-bridge')
SHARED_SKILLS_SOURCE = Path('__WSL_WORKSPACE__/skills-active')
WSL_PLUGIN = WSL_ROOT / 'profiles' / 'telegram' / 'plugins' / 'image_gen' / 'comfyui-bridge'

checks = {}

def env_keys(path: Path):
    if not path.exists():
        return []
    keys = []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        keys.append(line.split('=', 1)[0].strip())
    return keys

for rel in [
    PROFILE_SOURCE / 'config.yaml',
    PROFILE_SOURCE / '.env',
    PROFILE_SOURCE / 'README.md',
    G_ROOT / 'docs' / 'plans' / '2026-05-22-g-managed-telegram-profile.md',
    G_ROOT / 'scripts' / 'sync-g-managed-telegram-profile.sh',
    G_ROOT / 'scripts' / 'validate-g-managed-telegram-profile.sh',
    PLUGIN_SOURCE / '__init__.py',
    PLUGIN_SOURCE / 'plugin.yaml',
    SHARED_SKILLS_SOURCE / 'youtube' / 'SKILL.md',
    SHARED_SKILLS_SOURCE / '__PRIVATE_TOKEN_02__-authority-retrieval' / 'SKILL.md',
]:
    checks[str(rel)] = rel.exists()

config_text = (PROFILE_SOURCE / 'config.yaml').read_text(encoding='utf-8', errors='ignore') if (PROFILE_SOURCE / 'config.yaml').exists() else ''
default_model_text = (G_ROOT / 'docs' / 'telegram-default-model.yaml').read_text(encoding='utf-8', errors='ignore') if (G_ROOT / 'docs' / 'telegram-default-model.yaml').exists() else ''
default_model_match = __import__('re').search(r'^\s+model:\s*(.+?)\s*$', default_model_text, __import__('re').M)
canonical_telegram_model = default_model_match.group(1).strip() if default_model_match else ''
checks['config_has_comfy_provider'] = 'image_gen:\n  provider: comfyui-bridge' in config_text
checks['config_enables_plugin'] = 'image_gen/comfyui-bridge' in config_text
checks['config_has_shared_skills_external_dir'] = '__WSL_WORKSPACE__/skills-active' in config_text

source_env_keys = env_keys(PROFILE_SOURCE / '.env')
checks['source_env_has_telegram_keys'] = any(k.startswith('TELEGRAM_') for k in source_env_keys)
checks['source_env_has_comfyui_keys'] = any(k.startswith('COMFYUI_') for k in source_env_keys)
checks['source_env_has_civitai_keys'] = any(k.startswith('CIVITAI_') for k in source_env_keys)

checks['wsl_profile_exists'] = (WSL_ROOT / 'profiles' / 'telegram' / 'config.yaml').exists()
checks['windows_profile_exists'] = (WIN_ROOT / 'profiles' / 'telegram' / 'config.yaml').exists()
checks['wsl_plugin_link_exists'] = WSL_PLUGIN.exists()
checks['wsl_plugin_link_target_ok'] = WSL_PLUGIN.resolve() == PLUGIN_SOURCE.resolve() if WSL_PLUGIN.exists() else False
checks['wsl_active_profile_telegram'] = (WSL_ROOT / 'active_profile').read_text(encoding='utf-8', errors='ignore').strip() == 'telegram' if (WSL_ROOT / 'active_profile').exists() else False
checks['windows_active_profile_telegram'] = (WIN_ROOT / 'active_profile').read_text(encoding='utf-8', errors='ignore').strip() == 'telegram' if (WIN_ROOT / 'active_profile').exists() else False
checks['telegram_profile_api_port_8643'] = False
telegram_env = WSL_ROOT / 'profiles' / 'telegram' / '.env'
if telegram_env.exists():
    for line in telegram_env.read_text(encoding='utf-8', errors='ignore').splitlines():
        if line.startswith('API_SERVER_PORT='):
            checks['telegram_profile_api_port_8643'] = line.split('=', 1)[1].strip() == '8643'
            break

non_telegram_envs = [
    WSL_ROOT / '.env',
    WIN_ROOT / '.env',
    WSL_ROOT / 'profiles' / 'home' / '.env',
    WSL_ROOT / 'profiles' / 'gpt' / '.env',
    WIN_ROOT / 'profiles' / 'GPT' / '.env',
]
checks['non_telegram_envs_stripped'] = all(not any(k.startswith('TELEGRAM_') for k in env_keys(path)) for path in non_telegram_envs if path.exists())
checks['telegram_profile_env_has_telegram_keys'] = any(k.startswith('TELEGRAM_') for k in env_keys(WSL_ROOT / 'profiles' / 'telegram' / '.env'))
checks['telegram_profile_env_has_civitai_keys'] = any(k.startswith('CIVITAI_') for k in env_keys(WSL_ROOT / 'profiles' / 'telegram' / '.env'))
checks['canonical_telegram_default_model_exists'] = bool(canonical_telegram_model)
checks['source_config_uses_canonical_telegram_default_model'] = bool(canonical_telegram_model) and f'default: {canonical_telegram_model}' in config_text
checks['source_config_does_not_default_raw_gemma'] = 'default: gemma4-ollamauncensored:latest' not in config_text
checks['source_config_does_not_reference_stale_gemma_unrestricted'] = 'gemma4-unrestricted:9b' not in config_text

status = 'ok' if all(checks.values()) else 'error'
print(json.dumps({'status': status, 'checks': checks}, indent=2))
raise SystemExit(0 if status == 'ok' else 1)
PY
