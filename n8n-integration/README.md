# n8n-integration — wire an n8n automation server to the stack

**What it is:** templates and verification scripts for connecting
[n8n](https://n8n.io) (a self-hostable workflow-automation server) to this kit's
agent stack.

**Why you'd want it:** n8n is the easiest bridge between your agent and the
outside world — webhooks, schedules, and hundreds of ready-made integrations
(Telegram, Discord, Slack, email, Google services…). In this kit it plays two
roles:

1. **Messenger hub** — the fastest way to get the
   [`modules/messaging/`](../modules/messaging/) contract working with a real
   platform: one webhook workflow, swap in the trigger node for your messenger.
2. **Automation lane** — scheduled jobs and event-driven workflows that call your
   agent (or that your agent triggers) without custom glue code.

## What's inside

- `N8N-INTEGRATION-TEMPLATE.md` — the binding template: how an n8n instance
  declares its connection to the stack (URL, credential slots, webhook names).
  Credentials are *slots you fill*, never shipped values.
- `verify-n8n-binding.ps1` / `.sh` — read-only checks that the binding you
  configured actually reaches your n8n instance and its webhooks answer.

## Use

You need an n8n instance (self-hosted container or n8n cloud). Fill the template
with your instance's values, run the verify script, then build workflows against
the messaging contract. If you don't use n8n, skip this directory entirely —
nothing else depends on it.
