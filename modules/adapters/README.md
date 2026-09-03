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

`qualification.json` is the immutable Phase 76 receipt for the OpenCode and Pi
source set. It is historical after Phase 77 and must not be used to prove the
current adapter matrix. `qualification-phase77.json` is the current-source
receipt for the four-adapter matrix.

Run `python modules/adapters/verify-adapters.py` for the provider-free reference
adapter matrix.
