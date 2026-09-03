# Pi adapter

This adapter maps Pi extension events to the universal orchestration protocol.
It evolves the earlier request-projection extension: tool selection,
token-compute optimization, routing, authority, memory, retrieval, and provider
policy are shared-core operations rather than Pi-local policy.

The extension supplies a closed event with `event`, `operation`, `data`, and a
closed `session` object. Responses retain provider accounting and context
attribution. Source qualification does not modify Pi extensions, settings,
providers, models, tools, or live sessions.

Provider-free qualification:

```text
python modules/conformance-kit/run-conformance.py --harness-dir modules/adapters/pi
python modules/adapters/verify-adapters.py
```
