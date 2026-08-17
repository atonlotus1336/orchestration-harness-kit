# G-Managed Telegram Hermes Profile Implementation Plan

> For Hermes: use the hermes-agent and writing-plans skills. Execute this as a scaffold-only migration with no gateway restart.

Goal: Prepare a dedicated G-drive-managed Hermes profile that will become the only Telegram owner after the user restarts Hermes, and route image generation through the local ComfyUI bridge instead of the default FAL path.

Architecture: Keep __WINDOWS_WORKSPACE__ as the source of truth. Store a dedicated telegram profile scaffold and ComfyUI image plugin source on G:. Add a sync script that projects that source into both the Windows Hermes profile store and the live WSL runtime profile before startup. Remove Telegram env propagation from the generic cross-profile sync path so only the telegram profile receives the bot token.

Tech stack: Hermes profiles, WSL shell scripts, Windows profile mirror under C:\Users\__WINDOWS_USER__\.hermes, Hermes user plugins, local ComfyUI bridge script, YAML/.env config sync.

---

### Task 1: Create a G-drive source-of-truth profile scaffold

Objective: Add a dedicated telegram profile directory on G: with profile config, env template, and docs.

Files:
- Create: `__WSL_WORKSPACE__/hermes-agent-self-evolution/profiles/telegram/config.yaml`
- Create: `__WSL_WORKSPACE__/hermes-agent-self-evolution/profiles/telegram/.env`
- Create: `__WSL_WORKSPACE__/hermes-agent-self-evolution/profiles/telegram/README.md`

Steps:
1. Base the profile config on the current gpt runtime shape, not the home profile.
2. Set `image_gen.provider: comfyui-bridge`.
3. Add `plugins.enabled` entry for the user image plugin key `image_gen/comfyui-bridge`.
4. Put Telegram-only env keys in the G profile `.env`, not in generic shared env sync.
5. Keep ComfyUI env present in the profile so the bridge resolves without FAL.

Verification:
- The profile files exist on G:.
- The config contains `image_gen.provider: comfyui-bridge`.
- The env file contains `TELEGRAM_*` and `COMFYUI_*` keys.

### Task 2: Add a user image_gen plugin that calls the local ComfyUI bridge

Objective: Create a Hermes image provider plugin sourced from G: and exposed to WSL via `~/.hermes/plugins`.

Files:
- Create: `__WSL_WORKSPACE__/hermes-custom-plugins/image_gen/comfyui-bridge/plugin.yaml`
- Create: `__WSL_WORKSPACE__/hermes-custom-plugins/image_gen/comfyui-bridge/__init__.py`

Steps:
1. Implement an `ImageGenProvider` named `comfyui-bridge`.
2. Map Hermes aspect ratios to bounded ComfyUI width/height values.
3. Call `__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py run --preset txt2img ...`.
4. Parse bridge JSON output and return the first generated image path via `success_response`.
5. Return grounded `error_response` messages on missing bridge/script/output.

Verification:
- The plugin source exists on G:.
- The provider name is `comfyui-bridge`.
- The plugin can be discovered from the symlinked user plugin path.

### Task 3: Add a sync script that makes G: the authoritative startup source

Objective: Ensure startup always rehydrates the telegram profile from G: into Windows and WSL before Hermes starts.

Files:
- Create: `__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-g-managed-telegram-profile.sh`
- Modify: `__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-api.sh`

Steps:
1. Copy the G-managed telegram config/env into `C:\Users\__WINDOWS_USER__\.hermes\profiles\telegram` and `~/.hermes/profiles/telegram`.
2. Set both Windows and WSL `active_profile` to `telegram`.
3. Ensure `~/.hermes/profiles/telegram/plugins/image_gen/comfyui-bridge` points at the G plugin source.
4. Strip `TELEGRAM_*` keys from root/home/gpt and any other non-telegram runtime env files.
5. Call the sync script from startup before the existing profile-apply flow.

Verification:
- The sync script runs successfully without restarting the gateway.
- Windows and WSL telegram profile mirrors exist.
- Non-telegram profiles no longer retain `TELEGRAM_*` keys.

### Task 4: Stop generic profile sync from spraying Telegram credentials everywhere

Objective: Prevent home or other profiles from reclaiming the bot on the next startup.

Files:
- Modify: `__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh`
- Modify: `__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-startup-auth.sh`

Steps:
1. Remove `TELEGRAM_*` keys from the global env-sync allowlists.
2. Keep `COMFYUI_*`, API server, auth, and orchestration sync behavior intact.
3. Leave Telegram ownership to the dedicated G-managed profile sync script only.

Verification:
- Generic profile-sync scripts no longer contain `TELEGRAM_*` allowlist entries.
- ComfyUI/global orchestration sync remains present.

### Task 5: Review and verify the scaffold against takeover criteria

Objective: Confirm the code and files on G: satisfy the requested handoff before any restart.

Files:
- Review all created and modified files above.

Checks:
1. Home gateway will stop owning Telegram after restart because non-telegram env files no longer carry `TELEGRAM_*`.
2. The new G-managed `telegram` profile is the only runtime profile with Telegram env keys.
3. Startup always syncs from G: because `ensure-hermes-api.sh` calls the dedicated G sync script before boot.
4. Image generation is wired to ComfyUI because the telegram profile config selects `comfyui-bridge` and the plugin shells out to the local bridge script.

Non-goal:
- Do not restart Hermes or the gateway in this task.
