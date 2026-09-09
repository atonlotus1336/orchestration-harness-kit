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
T4 needs explicit autonomous intent. The recipient default keeps T2 gated to T3 until that runtime passes its paired
qualification; qualified local runtimes can promote T2 independently. Missing evidence
cannot qualify a lower execution lane. S0-S4 review floors are independent.
Classification makes no reviewer calls and never reports review completion.

Review packet admission is deterministic and all-or-nothing. The complete
serialized request for every required reviewer must fit the current per-request,
aggregate-input and model-call budgets. The shared runner reports packet hashes
and byte counts without copying source text into diagnostics. A refusal includes
the required input units and current limit in completion controls. It never
truncates files, removes reviewers, resets a budget, or changes S0-S4 selection.
An oversized delivery remains INCONCLUSIVE. Run the packet regressions with
`python -m pytest installer/tests/test_review_packets.py -q` from the kit root.

The completion controller binds each semantic review to the task objective,
invariants, acceptance criteria, and exact before/current/diff evidence. Deleted
paths retain their before bytes and explicit deletion state. Read-only Git blob
access refuses repository content filters first. Qualified automatic S4 receives
a sealed grant from the existing policy owner after its atomic claim; unresolved
or indeterminate usage stays on hold and cannot replay. These controls reuse the
selected review calls and add no evidence-only model invocation.

See ../../docs/v2/GOALS-AND-ROUTING.md for setup, evidence, exit codes, and
cross-harness continuation. Hosts must call the goalpost command at work
boundaries; native sessions do not acquire background timers from installation.

### Bounded T2 readiness

T2 can now be enabled through the shared execution contract after qualification;
it is no longer permanently hardcoded to T3. The recipient default remains
unpromoted until that recipient qualifies its own runtime. Eligible work needs one or two low-risk files and
a behavioral test or semantic assertions. See docs/v2/GOALS-AND-ROUTING.md for
the evidence, retry rules, and promotion procedure. Routing checks use no model.

### Host acceptance execution boundary

Pinned host acceptance tests require the host-owned evidence executor and its
enforced network, environment, output and process-tree restrictions. The portable
review package refuses execution when that owner is unavailable; it never falls
back to an unrestricted local pytest process. Recipients must supply their
compatible `lapart_core.evidence_adapter.run_host_acceptance` integration before
claiming host-test qualification. Installing this kit does not activate it.

### Review and goal integrity repairs

Review admission now reserves the complete reviewer set and fingerprints the
exact requests sent. A pre-dispatch file failure does not spend or poison the
model budget. Missing declared files remain INCONCLUSIVE, not a completed
deletion review. The bundled approval broker forwards its existing request ID
to the approval bridge; the bridge still validates the existing operator decision.

Goal checks require an authenticated `goal-assessment.v1` artifact from the
existing host review receipt owner. It binds `contract_hash`, `input_fingerprint`
and each check's `passed`, `method` and `assessor_id`. Pass the existing review
`Controller` as `evidence_owner` to `evaluate`. The trusted host must issue this
assessment from actual verification, never blindly sign caller labels. There
is no new signing service or public signer. Missing owners and unsigned claims
remain unknown; installing the port does not activate or wire a native issuer.

Run the installer goal/review tests and the worker module's `verify.py` to check
these contracts. Synthetic fixtures validate the code boundaries, not live
harness activation. Host-test refusal is tested separately from actual isolated
host-test execution, which requires the recipient's compatible evidence owner.
