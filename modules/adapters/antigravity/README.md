# Antigravity adapter

This adapter translates Antigravity CLI invocation events into the universal
orchestration protocol. The predecessor wrapper’s Prime-lane selection,
execution budgets, provider accounting, safety checks, and token-compute policy
are shared-core responsibilities in v2; the adapter retains only CLI event,
response, readiness, and rollback translation.

The local connector supplies a closed event with `command`, `operation`,
`arguments`, and `session`. Source qualification does not launch or modify the
Antigravity executable, select a model, make a provider request, edit settings,
or activate a live integration.

Provider-free qualification:

```text
python modules/conformance-kit/run-conformance.py --harness-dir modules/adapters/antigravity
python modules/adapters/verify-adapters.py
```
