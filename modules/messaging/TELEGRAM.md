# Telegram connection for Hermes

This path connects one Telegram bot to the Hermes chat gateway. Telegram is
optional. The solo-PC installation works without it.

## 1. Create the bot

1. Open Telegram and message `@BotFather`.
2. Run `/newbot` and follow its prompts.
3. Store the token in your operating system's secret storage or an untracked
   environment file. Never paste it into this repository.
4. Send one message to the new bot.

## 2. Find and allow your sender ID

Use Telegram's `getUpdates` API once, or an established ID helper bot, to read
the numeric `message.from.id` associated with your own test message. Treat the
number as access-control data. Set an explicit allowlist. An empty allowlist is
an error, not “allow everyone.”

## 3. Verify the adapter without secrets

```bash
python telegram-adapter.py --selftest
```

Expect JSON with an empty `failures` list.

## 4. Start one consumer

[PowerShell]

```powershell
$env:TELEGRAM_BOT_TOKEN='<secret-from-local-store>'
$env:TELEGRAM_ALLOWED_SENDER_IDS='<your-numeric-id>'
$env:GATEWAY_URL='http://127.0.0.1:8642/v1'
$env:GATEWAY_KEY='<local-gateway-key>'
$env:MODEL='<model-visible-through-hermes>'
python .\telegram-adapter.py
```

[bash]

```bash
export TELEGRAM_BOT_TOKEN='<secret-from-local-store>'
export TELEGRAM_ALLOWED_SENDER_IDS='<your-numeric-id>'
export GATEWAY_URL='http://127.0.0.1:8642/v1'
export GATEWAY_KEY='<local-gateway-key>'
export MODEL='<model-visible-through-hermes>'
python telegram-adapter.py
```

Keep this process visible. If you later register it as a service or scheduled
task, keep logs visible and record the exact secret-loading source.

## 5. Smoke test

Send: `Reply with exactly: TELEGRAM-OK`

Pass only when Telegram returns `TELEGRAM-OK` and the Hermes audit/session log
shows the request. An unknown sender ID must receive no reply.

## Failure map

- `401`: the Telegram token is wrong or revoked.
- `Conflict: terminated by other getUpdates`: another process or webhook owns
  this bot. Stop the duplicate. One bot token has one consumer.
- `backend unreachable`: Hermes gateway is not running or the URL/bind is wrong.
- Backend `401`: the Hermes gateway key is wrong.
- Timeout on first request: the selected local model may still be loading.

For a VPS-hosted adapter, keep the home PC gateway behind an authenticated
private overlay. Network membership does not replace the gateway key.
