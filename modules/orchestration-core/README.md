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

The required `modules/token-compute-optimization` module supplies the portable
activation registry and the complete safe control profile. Marginal-value
stopping stays shadow-only, and insufficient evidence always selects full depth.

## Provider lease and backend admission

`ProviderRoute` combines a credential-free `ProviderLease` with a
`BackendContract`. `provider.resolve` can therefore reject a route before any
model call when its authentication mode, distribution context, retention,
compliance class, task shape, durable-resume capability, latency ceiling, or
version range does not satisfy the request. A subscription route cannot serve a
distributable product through delegated subscription authentication unless its
lease explicitly permits that use.

Compatibility and terms changes set qualification to `stale` until the existing
qualification owner refreshes the route. The closed portable JSON shape is
documented in `provider-route.schema.json`; credentials never belong in it.

This admission result is independent of T0-T4 execution routing and S0-S4 review
depth. It selects a compatible backend for an already-authorized lane; it does
not authorize execution or promote review policy.

Telemetry accepts only total latency and non-negative content-free stage timings
for adapter, routing, provider wait, tool execution, handoff, evidence collection,
and persistence. Provider-native tracing is not required and must not become a
second evidence authority.

## Goalposts and independent review

The installed core includes goalposts.py, a shared file-based evaluator with
finite endpoints, weighted boolean checks, an 80/100 usability floor, independent
judgment, active-time checkpoints, and regression protection. Start with
goal-contract.example.json. Run verify-core.py --selftest to test this part.

routing.prime.select returns separate execution and review classifications.
T4 needs explicit autonomous intent. T2 remains gated to T3. Missing evidence
cannot qualify a lower execution lane. S0-S4 review floors are independent.
Classification makes no reviewer calls and never reports review completion.

See ../../docs/v2/GOALS-AND-ROUTING.md for setup, evidence, exit codes, and
cross-harness continuation. Hosts must call the goalpost command at work
boundaries; native sessions do not acquire background timers from installation.
