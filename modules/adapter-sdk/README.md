# Universal harness adapter SDK

**What it is:** the small, public boundary between any AI harness and the shared
LapArt orchestration core.

**Why you would want it:** a harness author declares capabilities in one manifest
and implements six small translation/lifecycle methods. Routing, authority, memory, retrieval,
skills, tools, token optimization, provider policy, activation, and rollback stay
inside the shared core.

## Author a thin adapter

1. Copy `templates/harness-manifest.json` and `templates/adapter.py`.
2. Change only portable identity, capability, operation, and entry-point fields.
3. Keep credential values in the recipient's environment or secret store; the
   manifest contains references only.
4. Run `python verify-adapter-sdk.py --selftest`.

Adapters translate local events into `AdapterRequest` values and core responses
back into the harness format. They must not implement shared policy or modify
`core-policy.json`.

## Provider-free verification

```bash
python verify-adapter-sdk.py --selftest
```

The verifier negotiates a fake harness, sends requests through the real shared
core, checks portability mutations, and asserts zero provider calls.
