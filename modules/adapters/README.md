# Reference adapters

Each directory contains one declarative harness manifest and a translation-only
adapter for the Universal Orchestration SDK v2. Shared policy stays in
`modules/orchestration-core`; these adapters only normalize harness-local events,
responses, readiness, and rollback signals.

Adapters are opt-in installer modules. Source qualification proves the portable
contract without changing a live harness. Runtime installation, shadow testing,
and activation remain separate states.

- `hermes/` — canonical Hermes OS reference
- `opencode/` — OpenCode plugin-event translation
- `pi/` — Pi extension-event translation
- `antigravity/` — Antigravity CLI-event translation
- `codex/` — Codex notification translation
- `claude-code/` — Claude Code hook-event translation
- `moa/` — MOA phase-event translation
- `cursor/` — Cursor JSON-hook translation plus an opt-in plugin template

`qualification.json`, `qualification-phase77.json`, and
`qualification-phase78.json` are immutable historical receipts. Current adapter
validity comes from each adapter's source-hash manifest plus a fresh provider-free
conformance run. A later installer change must not rewrite or falsely invalidate
that historical evidence.

Run `python modules/adapters/verify-adapters.py` for the provider-free reference
adapter matrix, including Cursor.
