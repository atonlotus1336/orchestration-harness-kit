# G-managed Telegram Hermes profile

This directory is the source of truth for the dedicated `telegram` Hermes profile.

For low-token cross-doc troubleshooting, start with
`__WINDOWS_WORKSPACE__\hermes-webui\hermes-workspace\docs\LLM-TROUBLESHOOTING-ROUTER.md`.

Canonical default model route: `__WINDOWS_WORKSPACE__\hermes-agent-self-evolution\docs\telegram-default-model.yaml`.
When Telegram's default model changes, edit that file first, then update this
profile config and run `scripts\validate-telegram-default-model.sh`.

Model/profile switching policy: see
`__WINDOWS_WORKSPACE__\hermes-agent-self-evolution\docs\model-profile-switching-contract.md`.
For Telegram, requests such as "switch to DeepSeek", "use Qwen", or "use GPT"
mean switch the model/provider route inside the `telegram` profile. They do not
mean move the Telegram gateway to another Hermes profile unless the user
explicitly asks to change gateway/profile ownership.

Use the shared Hermes skill `telegram-switch-models` for these requests. Its
script is:
`__WSL_WORKSPACE__/.hermes/skills/autonomous-ai-agents/telegram-switch-models/scripts/switch_telegram_model.py`.

What it owns:
- Telegram bot credentials and channel routing
- ComfyUI bridge configuration for image generation
- Civitai API credentials for ComfyUI model downloads
- Hermes profile config that selects the `comfyui-bridge` image backend
- Model switch aliases for Telegram sessions, including `ollama-gemma-unrestricted`

How it is applied:
- `scripts/sync-g-managed-telegram-profile.sh` mirrors this directory into:
  - `C:\Users\__WINDOWS_USER__\.hermes\profiles\telegram`
  - `~/.hermes/profiles/telegram`
- The same sync script also points `~/.hermes/profiles/telegram/plugins/image_gen/comfyui-bridge` at the plugin source on G: and strips Telegram credentials from non-telegram profiles.
- `scripts/ensure-hermes-api.sh` runs that sync step before the normal Hermes startup/profile-apply flow.

Expected behavior after restart:
- `home` no longer owns the Telegram bot token
- `telegram` becomes the only profile with `TELEGRAM_*` runtime env keys
- Hermes image generation for that profile routes to the local ComfyUI bridge instead of FAL
- Default chat route must match `docs\telegram-default-model.yaml`
- Stable cloud route remains available as `ollama-gemma-cloud` -> `gemma4:31b-cloud`

## Quick health and recovery

Telegram uses a dedicated gateway port on this install:

```text
telegram gateway: http://127.0.0.1:8643/health/detailed
```

If Telegram stops answering, do not rely on stale `gateway_state.json`. Check
the live endpoint and listening port:

```bash
curl -sf http://127.0.0.1:8643/health/detailed
ss -ltnp | grep 8643
cat ~/.hermes/active_profile
```

Healthy state:

```text
active_profile = telegram
platforms.api_server.state = connected
platforms.telegram.state = connected
```

If `8643` is down, re-apply the managed Telegram profile. This syncs runtime
code, preserves the global orchestration layer, pins the Telegram API server to
`8643`, and restarts the gateway as `--profile telegram`:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh telegram
```

Then verify:

```bash
curl -sf http://127.0.0.1:8643/health/detailed
```

To send a private Telegram smoke-test message without printing credentials:

```bash
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/send-telegram-test-message.py
```

Expected output:

```json
{
  "ok": true,
  "message_id": 5009,
  "chat_type": "private"
}
```

The exact `message_id` will change. If this succeeds, outbound Telegram
delivery is working; reply to the bot to confirm inbound gateway handling.

Do not set the unrestricted model as a bare scalar such as:

```yaml
model: gemma4unrestricted
```

That drops provider context and can route the model through `openai-codex`,
causing a Codex 400. Keep the nested default route and use the alias/model
picker for `gemma4-ollamauncensored-32k:latest`.

Do not point Telegram at raw `gemma4-ollamauncensored:latest`. On this machine,
Ollama has tried to load that raw model with `num_ctx=262144`, which crashed the
local runner with:

```text
HTTP 500: model failed to load
GGML_ASSERT(n_inputs < GGML_SCHED_MAX_SPLIT_INPUTS) failed
```

The sync script runs `scripts/ensure-telegram-ollama-model.sh --warn-only` to
recreate the safer local alias when Ollama is reachable. To force a checked
recreation and smoke test:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-telegram-ollama-model.sh --required --smoke
```

## Known profile-drift failure: image exists but Telegram token is missing

Symptom:

```text
TELEGRAM_BOT_TOKEN is not configured
```

or a Telegram-facing message says the script knows where the generated image is
but does not have the key to send it through the messaging platform.

Cause: ComfyUI generation can run from any active Hermes profile, but Telegram
credentials intentionally live only in the dedicated `telegram` profile. If
`~/.hermes/active_profile` drifts to something like `gemmalocal`, the image can
be generated successfully while the send step has no `TELEGRAM_BOT_TOKEN`.

Expected bridge behavior after the 2026-05-23 fix:

- `comfyui_bridge.py` honors `HERMES_HOME` when callers explicitly run with
  `HERMES_HOME=/home/__LINUX_USER__/.hermes/profiles/telegram`.
- If the active profile has no Telegram token, `comfyui_bridge.py` falls back to
  `~/.hermes/profiles/telegram/.env` for Telegram delivery.
- `comfyui_bridge.py status` should report `telegram.bot_token_configured = true`
  even if the active profile is not `telegram`.

Recovery checks:

```bash
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py status
```

If `telegram.bot_token_configured` is false, re-sync this managed profile:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-g-managed-telegram-profile.sh
```

Then verify by resending a known generated image:

```bash
python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py send-file __WSL_WORKSPACE__/hermes-agent-self-evolution/output/comfyui/example.png --allow-resend --telegram-caption "Hermes bridge token verification"
```

Do not hand-edit Telegram credentials in `~/.hermes/profiles/*/.env` or `C:\Users\__WINDOWS_USER__\.hermes\profiles\*\.env`.
Edit this G-drive source instead, then re-run the sync script.

## Known restart failure: `gateway.restart.restarting` but no Telegram comeback

Symptom:

```text
gateway.restart.restarting
```

or the user sees the "restarting" acknowledgement in Telegram, but no later
"Gateway restarted successfully" / "Gateway online" message arrives and the bot
stops responding.

Expected behavior after the restart safeguards:

- `/restart` writes `~/.hermes/profiles/telegram/.restart_notify.json` before
  shutdown so the next gateway can notify the requesting chat/thread.
- `/restart` writes `~/.hermes/profiles/telegram/.restart_last_processed.json`
  so Telegram redelivery of the same update does not create a restart loop.
- Active sessions are marked `resume_pending` before drain/interrupt so work can
  continue or recover on the next gateway boot.
- Startup sends the direct restart notification, then optionally falls back to
  configured home-channel startup notifications.
- `scripts/ensure-hermes-api.sh` re-syncs this G-managed profile before startup
  and starts the gateway if `127.0.0.1:8642/health` is down.

Recovery checks:

```bash
hermes gateway status
cat ~/.hermes/active_profile
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/validate-g-managed-telegram-profile.sh
tail -200 ~/.hermes/logs/gateway.log
```

Healthy local state should show:

```text
Gateway is running
active_profile = telegram
validate-g-managed-telegram-profile.sh status = ok
```

If the gateway is not running, use the workspace startup path or:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/ensure-hermes-api.sh
```

If Telegram still does not answer after the gateway is running, check whether
the restart notification send failed due to Telegram/network timeout. That is a
delivery problem, not a gateway resurrection problem; inspect `gateway.log` for
`telegram.error.TimedOut`, `send failed`, or `Restart notification ... was not
delivered`.

## Known auth failure: Civitai token is missing

Symptom:

```text
CIVITAI_API_KEY is not configured
```

or a Civitai download reports an authentication/token error.

Cause: Civitai credentials are profile-scoped with the same managed `telegram`
profile used by the ComfyUI bridge. Generic profiles are not expected to own
the key.

Recovery:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-g-managed-telegram-profile.sh
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/validate-g-managed-telegram-profile.sh
HERMES_HOME=/home/__LINUX_USER__/.hermes/profiles/telegram python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py catalog
```

Expected:

```text
source_env_has_civitai_keys = true
telegram_profile_env_has_civitai_keys = true
civitai.api_key_configured = true
```

Do not paste Civitai API keys into docs, prompts, logs, or generic Hermes
profiles. Edit this G-drive source `.env`, then re-run the sync script.
