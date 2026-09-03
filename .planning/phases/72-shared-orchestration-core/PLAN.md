# Phase 72 — Shared Orchestration Core

## Goal

Implement one provider-free core that owns routing, authority, capability discovery,
context optimization, provider policy, accounting, health, activation state,
rollback, and verification for every adapter.

## Dependencies

- Phase 71 operation catalog and envelope contract
- Phase 70 version negotiation
- Immutable routing, context, provider, and promotion sources pinned in Phase 69

## Source and output files

- Sources: pinned Phase 49–68 commits and the Phase 71 protocol
- Outputs: shared-core package, closed policy, deterministic verifier, focused
  tests, source hashes, and verification receipt

## Acceptance criteria

1. The core has a handler for all 31 protocol operations.
2. Prime routing deterministically reports recommended and actual T0–T4 lanes.
3. Authority decisions fail closed and require approval for external effects.
4. Tools, skills, and MCP servers stay discoverable through deferred details.
5. Context selection never removes required authority or safety content.
6. Provider resolution remains subscription-first and forbids pay-per-token routes
   without explicit policy and approval.
7. Every response reports provider-call accounting, including explicit zeros.
8. Activation and rollback operate only on isolated in-memory state in this phase.
9. Tests prove adapters cannot inject or replace core policy.
10. The provider-free envelope regression sentinel passes.

## Threat model

- Harness input could attempt to override core policy.
- Context savings could hide required authority or capability discovery.
- Provider fallback could incur unapproved cost.
- Accounting could omit an actual call.
- Qualification could be mistaken for production activation.
- Mutable or stale source evidence could support a false closure claim.

## Rollback

Remove the shared-core module and its module-manifest row. The committed Phase 71
protocol and Phase 70 v1 compatibility path remain intact.

## Budget and activation

- Provider calls: 0
- Token budget: 0 provider tokens
- Service restarts: 0
- Runtime activation impact: none
