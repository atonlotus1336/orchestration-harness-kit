# Hermes Model/Profile Switching Contract

Purpose: prevent profile/model confusion from breaking Telegram, Workspace, MCP, RAG, memory, or skill paths.

## Core Rule

When the user asks to switch models, treat it as a model/provider route change, not a Hermes profile change.

Profile names are implementation details. The user cares which LLM is answering. Gateway ownership, credentials, runtime paths, sessions, and tool wiring should stay stable unless the user explicitly asks to move those.

## Terms

- Model route: provider plus model, for example `ollama-cloud` plus `deepseek-v4-pro:cloud`.
- Profile: Hermes runtime home, for example `telegram`, `gpt`, or `deepseekv4`.
- Gateway owner: the profile whose gateway process owns a messaging/API surface.

## Default Behavior

For Telegram:

- The canonical Telegram default model route lives in:
  `__WINDOWS_WORKSPACE__\hermes-agent-self-evolution\docs\telegram-default-model.yaml`.
  Change that file first, then update/sync/apply the Telegram profile and run
  `validate-telegram-default-model.sh`.
- Keep the gateway owner as `telegram`.
- Switch model routes inside the `telegram` profile.
- Do not switch the live Telegram gateway to `deepseekv4`, `gpt`, `gemmalocal`, or any other profile just because the user said "use DeepSeek", "use GPT", or "use Qwen".
- Keep Telegram on API port `8643`.
- Keep `deepseekv4` or other non-Telegram gateways on their own ports, usually `8642` for the generic Workspace/API gateway.

For Workspace/API chat:

- A profile switch may be appropriate when the user is working in Workspace profile UI or asks for a specific workspace/profile.
- Even then, `providers`, `mcp_servers`, `memory`, and `toolsets` must remain present after the switch.

## User Intent Mapping

Model-route requests:

- "switch to DeepSeek V4"
- "use Qwen"
- "use Claude"
- "use Codex"
- "use Gemma local/cloud"
- "change the LLM"

Action: keep the current gateway profile and update the model/provider route or use a configured model alias.

Profile/gateway requests:

- "run the DeepSeekV4 profile gateway"
- "make Workspace use the GPT profile"
- "move Telegram ownership to another profile"
- "restart the gateway under profile X"

Action: switch or launch the profile only after confirming the target gateway owner and expected ports.

Ambiguous requests:

- "switch profile"
- "use the DeepSeek profile"

Action: ask whether the user means model route or gateway/profile ownership. Do not change `active_profile` or restart gateways until clarified.

## Telegram Safety Invariants

Telegram is healthy only when:

- `telegram` gateway process is running explicitly with `--profile telegram`.
- `http://127.0.0.1:8643/health/detailed` reports both `api_server` and `telegram` connected.
- `~/.hermes/profiles/telegram/.env` has `API_SERVER_PORT=8643`.
- `TELEGRAM_*` keys exist only in the Telegram profile `.env`.
- `skills.external_dirs` points at `__WSL_WORKSPACE__/.hermes/skills`.
- `memoryweaver-mcp` and `rag-orchestrator` are visible in `hermes mcp list`.

## Recovery Commands

Preferred Telegram model switch path:

```bash
python3 __WSL_WORKSPACE__/.hermes/skills/autonomous-ai-agents/telegram-switch-models/scripts/switch_telegram_model.py "deepseek v4"
```

Use this skill/script for Telegram model-route changes instead of hand-editing
profiles or running profile commands.

Validate Telegram profile source and mirrors:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/sync-g-managed-telegram-profile.sh
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/validate-g-managed-telegram-profile.sh
```

Restart Telegram without stealing the generic gateway:

```bash
__WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh \
  telegram glm-5.2:cloud ollama-cloud
```

Verify live state:

```bash
curl -sf http://127.0.0.1:8643/health/detailed
hermes gateway list
ss -ltnp | grep -E '8642|8643'
```

Expected:

- `telegram` gateway running on `8643`.
- Generic non-Telegram gateway, if any, running separately on `8642`.

## Implementation Guidance

- Prefer model aliases in the active profile over profile changes.
- Launch gateways with explicit `--profile <name>`.
- Never rely on global `active_profile` alone when starting a gateway.
- Profile-apply scripts must export the target profile `.env` before launching, because API server settings are environment-driven.
- Keep G: as the managed source of truth and materialize Windows/WSL runtime mirrors from it.
