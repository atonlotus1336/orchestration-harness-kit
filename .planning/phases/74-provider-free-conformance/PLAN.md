# Phase 74 — Provider-Free Conformance Kit

## Goal

Prove that a new harness can connect through only a declarative manifest and
thin adapter, exercise the full orchestration protocol, and preserve shared-core
behavior without modifying or copying the core.

## Dependencies

- Phase 71 protocol and response verifier
- Phase 72 shared core and fixed-envelope sentinel
- Phase 73 adapter SDK, manifest loader, and universal bridge

## Source and output files

- Sources: protocol catalog, shared core, adapter SDK, and their source hashes
- Outputs: conformance runner, deterministic suite, fake harness manifest and
  adapter, boundary mutations, focused tests, source hashes, and verification

## Acceptance criteria

1. The fake harness connects without any shared-core modification.
2. Its adapter imports the adapter SDK, never the shared core.
3. All 31 operations traverse the universal bridge and validate as protocol
   responses.
4. Provider accounting is present and zero on every conformance response.
5. Context attribution, actual Prime lane, retrieval attribution, activation,
   rollback, and source-hash verification receive focused assertions.
6. Static boundary checks reject adapter-owned routing, authority, provider,
   memory, retrieval, optimization, or activation logic.
7. Deterministic mutations reject missing operations, dropped accounting,
   core imports, credential fields, and stale source hashes.
8. The runner works from a clean public payload with no network or provider use.

## Threat model

- A passing adapter could bypass the bridge for selected operations.
- A local response could discard cost or context evidence.
- Fake conformance could silently skip difficult operations.
- Adapter code could import or copy shared policy.
- Stale fixtures or hashes could make a closure claim non-reproducible.

## Rollback

Remove the conformance-kit row and module. Protocol, core, and adapter SDK remain
usable and independently verified.

## Budget and activation

- Provider calls: 0
- Provider tokens: 0
- Service restarts: 0
- Runtime/profile activation impact: none
- Coding-plan routing: provider-free classification only
