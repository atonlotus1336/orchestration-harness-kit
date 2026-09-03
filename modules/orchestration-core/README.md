# Shared orchestration core

**What it is:** one deterministic core that applies LapArt routing, safety,
capability, context, provider, accounting, health, activation, and rollback policy
for every harness adapter.

**Why you would want it:** adapters stay small and interoperable because they
translate their harness format instead of copying orchestration logic.

## Install only this part

Copy this directory beside `modules/orchestration-protocol` and the kit's
`installer` package. Do not edit `core-policy.json` inside a harness adapter.
Recipient-specific provider, credential, and path references belong in ignored
local configuration.

## Test it worked

Run:

```bash
python verify-core.py --selftest
```

The verifier executes every protocol operation against isolated in-memory ports,
checks provider-free accounting, tests the fixed-envelope sentinel, and exercises
fail-closed policy mutations. It does not start a service or call a provider.

## Shared tool projection

`optimization.plan` owns deterministic request classification and tool projection
when `request_text` and `available_tools` are supplied together. Explicit
read-only requests keep safe read tools plus every discovery-prefixed tool;
mutating, empty, or ambiguous requests retain the full tool set. Authority and
capability catalogs are never removed to reduce context size.
