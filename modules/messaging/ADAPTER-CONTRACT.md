# Messenger Adapter Contract — ship the port, not the plug

The core of this kit never knows which messenger you use. Any chat surface
(Telegram, Discord, Slack, WhatsApp-via-Twilio, SMS, email, a web form)
connects through the same three-step contract:

```
inbound message  ->  authenticate  ->  POST /v1/chat/completions  ->  reply
```

The backend is ANY OpenAI-compatible endpoint. Two you already have with this
kit's reference setup:

| Backend | URL (example) | Auth |
|---|---|---|
| Hermes gateway | `http://<gateway-host>:8642/v1` | `Authorization: Bearer <API_SERVER_KEY>` |
| Ollama (direct) | `http://<ollama-host>:11434/v1` | none by default |

Because both speak the same API, an adapter written once works against either
— and against OpenAI/Anthropic-compatible proxies — by changing ONE env var.

## The contract (all of it)

1. **Inbound:** adapter receives `{text, sender_id}` from its messenger.
2. **Auth (two directions):**
   - Messenger→adapter: verify the sender (allowlist of sender IDs — deny by
     default; an open adapter is an open shell into your agent).
   - Adapter→backend: send the backend's API key. A private network
     (Tailscale/LAN) is NOT authentication — always send the key.
3. **Call:** `POST $GATEWAY_URL/chat/completions` with
   `{"model": $MODEL, "messages": [{"role":"user","content": text}]}`.
   Session/memory behavior is the backend's job, not the adapter's — keep
   adapters stateless where possible.
4. **Reply:** return `choices[0].message.content` to the sender. Map errors
   to distinct user-facing causes (connection refused = backend down;
   401 = key; timeout = model loading) — never a generic "error".

## Reference implementations in this module

- `generic-adapter.py` — stdlib-only Python; CLI one-shot or stdin JSON
  (webhook body). The smallest possible correct adapter; also the test rig.
- `n8n-adapter-template.json` — importable n8n workflow skeleton
  (Webhook → HTTP Request → Respond). n8n's node catalog (Telegram, Discord,
  Slack, WhatsApp, email, SMS providers) then gives you every messenger it
  supports without this kit naming any of them. Wire your credentials in n8n
  itself, never in this file. See also `../../n8n-integration/` for n8n binding checks.

## Design rules (inherited from the source system's incident history)

- Deny-by-default sender allowlist. Unknown sender: no reply, log only.
- The adapter never executes commands itself — it relays text to the backend
  and relays text back. Tool use, file access, and approvals live behind the
  backend's own gates (see `../guards/`).
- One adapter per messenger per backend. Two adapters sharing one bot token
  produces duelling-consumer conflicts (the source system lost weeks to a
  second Telegram consumer: "Conflict: terminated by other getUpdates").
- Keep the dashboard/admin surface OFF this path. Adapters speak to the
  chat endpoint only.

## Verify (out of the box)

```bash
# against any OpenAI-compatible backend you have running:
GATEWAY_URL=http://127.0.0.1:11434/v1 MODEL=<an-installed-model> \
  python generic-adapter.py "Say OK and nothing else."
# expect: a one-line model reply on stdout, exit 0
# negative control: point GATEWAY_URL at a dead port -> distinct
# "backend unreachable" error, exit 2 (never a stack trace)
```
