# messaging — text your agent from any messenger

**What it is:** a tiny contract plus a reference implementation that connects ANY
messaging platform (Telegram, Discord, Slack, WhatsApp, SMS, email…) to your AI
agent. The design principle: **ship the port, not the plug** — the kit defines the
socket every messenger plugs into, and never hard-codes any particular app.

**Why you'd want it:** your agent runs on a PC; your life happens on a phone. One
adapter turns "SSH into the machine" into "send a text."

## The pieces

- `ADAPTER-CONTRACT.md` — the whole interface in one page: receive message →
  authenticate → call your agent's OpenAI-compatible endpoint with a session →
  send the reply back. Anything that does those four steps is an adapter.
- `generic-adapter.py` — a working reference adapter (stdin/CLI based) you can run
  today and copy when writing a platform-specific one:
  ```bash
  GATEWAY_URL=http://localhost:8642/v1 GATEWAY_KEY=yourkey \
    python generic-adapter.py "hello from my phone"
  ```
- The **n8n route** (see [`../../n8n-integration/`](../../n8n-integration/)): if you
  run n8n, one webhook workflow gives you every messenger n8n supports — Telegram,
  Discord, Slack, WhatsApp — without writing any adapter code.

- The **direct Telegram route**: `telegram-adapter.py` plus `TELEGRAM.md` gives
  a complete deny-by-default long-poll setup without requiring n8n.

## Install just this part

You need one thing already running: an OpenAI-compatible endpoint for your agent
(Hermes gateway, Ollama, LM Studio, or any API proxy). Then:

1. Test the reference adapter against it (command above).
2. Pick your platform and either adapt `generic-adapter.py` (~50 lines) or import
   the n8n template and swap in your platform's trigger node.
3. For Telegram, follow [`TELEGRAM.md`](TELEGRAM.md) and run
`python telegram-adapter.py --selftest` before loading any token.

4. Keep the auth rule from the contract: **network reachability is never
   authentication** — the adapter always presents the API key.
