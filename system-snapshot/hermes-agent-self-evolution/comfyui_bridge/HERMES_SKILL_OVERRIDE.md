## WorkspaceKit Hermes Workspace Override

On this machine, Hermes Workspace uses the G-drive Option 5 bridge instead of direct generic comfy-cli execution for Workspace image generation and Telegram delivery. Use this path when the request mentions Hermes Workspace, Telegram, or sending ComfyUI output to Telegram.

Canonical bridge entrypoint:

```bash
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py status
```

Primary commands:

```bash
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py start
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py run --preset txt2img --prompt "..." --send-telegram
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py send-latest
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py send-file /absolute/path.png --allow-resend
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py check-deps --preset txt2img
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py download-civitai --url "https://civitai.com/models/..." --model-type checkpoint
```

Expected active profile for Telegram delivery:

```text
/home/__LINUX_USER__/.hermes/profiles/telegram
```

If bridge status reports `profile = gpt` or `telegram.bot_token_configured = false`, do not continue with generic ComfyUI scripts first. Re-apply and validate the G-managed profile:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-g-managed-telegram-profile.sh
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/validate-g-managed-telegram-profile.sh
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh telegram gemma4:31b-cloud ollama-cloud
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py status
```

Expected apply output includes `runtime import preflight OK`. That preflight
guards against mixed WSL runtime package versions before the gateway restarts.

If bridge status reports only `comfyui.live_host = null`, the profile may be
fine and Windows ComfyUI is probably not reachable. Start the Windows ComfyUI
portable launcher first, then rerun bridge status.

If a Civitai download reports `CIVITAI_API_KEY is not configured` or an
authentication/token error, do not paste the key into chat, code, docs, or
generic profile env files. The key belongs in the G-managed Telegram profile:

```text
__WSL_WORKSPACE__/hermes-agent-self-evolution/profiles/telegram/.env
```

Expected status:

```text
civitai.api_key_configured = true
```

Recovery:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-g-managed-telegram-profile.sh
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/validate-g-managed-telegram-profile.sh
HERMES_HOME=/home/__LINUX_USER__/.hermes/profiles/telegram python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py catalog
```

The validator should report `source_env_has_civitai_keys = true` and
`telegram_profile_env_has_civitai_keys = true`. The catalog should report
`civitai.api_key_configured = true` without printing the secret value.

Operating rules for this machine:

- `TELEGRAM_*` keys belong only to the dedicated `telegram` profile. Do not copy the bot token into `gpt`, `home`, or root `.env`.
- `CIVITAI_*` keys also belong in the dedicated `telegram` profile for this ComfyUI bridge.
- The sync script must preserve `API_SERVER_*` keys in `/home/__LINUX_USER__/.hermes/profiles/telegram/.env`.
- Profile/model switch typos must fail closed. Unknown profile names should not
  create profile directories or write `active_profile`; unknown model names
  should ask which model was intended when the provider's live model list proves
  the name is absent.
- ComfyUI runs on Windows from `G:\Storage\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\start_comfy_hermes.bat` and is reached from WSL at `http://10.0.0.1:8188`.
- Use API mode for Hermes-initiated generation. Use folder-fallback only for manually generated images or delivery recovery.
- Use `--allow-resend` when deliberately testing the same image file again.
- Check `__WSL_WORKSPACE__/hermes-agent-self-evolution/output/comfyui/bridge_log.jsonl` for delivery, dependency, and generation errors.

Related operator docs:

- `__WSL_WORKSPACE__/hermes-webui/hermes-workspace/docs/comfyui-hermes-workspace-setup.md`
- `__WSL_WORKSPACE__/hermes-webui/hermes-workspace/docs/telegram-comfyui-setup.md`
- `__WSL_WORKSPACE__/hermes-agent-self-evolution/comfyui_bridge/README.md`
