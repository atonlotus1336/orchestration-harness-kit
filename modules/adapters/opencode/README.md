# OpenCode adapter

This adapter maps OpenCode plugin hooks to the universal orchestration protocol.
It replaces the earlier request-projection design as the v2 integration target:
classification, tool selection, token-compute optimization, routing, safety, and
provider policy now belong to the shared core.

The local plugin supplies a closed event with `hook`, `operation`, `payload`,
`session_id`, and `session_state`. The translated response preserves provider
accounting and context attribution. The source-qualified module does not install
or edit an OpenCode plugin, configuration, provider, model, or live session.

Provider-free qualification:

```text
python modules/conformance-kit/run-conformance.py --harness-dir modules/adapters/opencode
python modules/adapters/verify-adapters.py
```
