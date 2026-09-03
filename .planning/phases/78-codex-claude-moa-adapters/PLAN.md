# Phase 78 — Codex, Claude Code, and MOA Adapters

## Goal

Add three translation-only adapters to the existing universal protocol without
copying governed execution, MOA reasoning, or any shared orchestration policy.

## Dependencies and immutable sources

- Phase 77 commit `9250671` plus protocol, core, adapter SDK, and conformance kit
- Governed-runtime adapter commit `a88e13c4a574653b97c28f079ef20e49b6f2b17a`
- Hermes MOA commit `aa605b66c89cdc021b4f08105a763e79830bcb04`
- Locally observed provider-free CLI versions: Codex `0.147.0`, Claude Code `2.1.211`

## Exact outputs

- `modules/adapters/codex/`
- `modules/adapters/claude-code/`
- `modules/adapters/moa/`
- aggregate compatibility, installer, provenance, qualification, and test updates

## Acceptance criteria and independent verification

1. Each manifest negotiates protocol v2 and exposes all 31 operations.
2. Each adapter translates only local event, response, lifecycle, health, and
   rollback representations.
3. Shared routing, authority, discovery, memory, retrieval, optimization,
   provider policy, accounting, and evaluation remain absent from adapters.
4. All three inherit identical core routing, projection, accounting, and context
   attribution in provider-free conformance.
5. Source maps bind every predecessor to immutable Git commits and blobs.
6. Installer modules are optional and leave the default install path unchanged.
7. The aggregate verifier and clean-build reproduction pass independently.

## Threat model

- Copying governed-runtime or MOA loops would create a second orchestration core.
- Native hook/notification surfaces could be overstated as enforcement authority.
- Local CLI versions could be confused with live activation evidence.
- Dirty source repositories could contaminate the public SDK.
- A historical Phase 77 receipt could be reused after source changes.

## Rollback, cost, and activation impact

Rollback removes the three optional adapter directories and their manifest rows.
Previously committed adapters and every live harness remain unchanged.

- Provider calls and tokens: 0
- Pay-per-token and DSH calls: 0
- Service restarts: 0
- Live configuration, model, provider, or activation changes: 0
- Evidence: a new Phase 78 current-source receipt; older receipts stay historical
