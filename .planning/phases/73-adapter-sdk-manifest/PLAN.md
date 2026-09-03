# Phase 73 — Adapter SDK and Harness Manifest

## Goal

Provide one declarative manifest and one thin adapter interface that let a new
harness translate local events into the shared protocol without copying shared
orchestration policy.

## Dependencies

- Phase 71 universal orchestration protocol
- Phase 72 shared orchestration core
- Phase 70 protocol-version negotiation

## Source and output files

- Sources: version negotiation library, protocol catalog, shared core, existing
  harness bindings, and messaging adapter contract
- Outputs: adapter SDK package, closed manifest schema, reference adapter,
  authoring templates, provider-free verifier, focused tests, and source hashes

## Acceptance criteria

1. A manifest declares identity, entry point, versions, operations,
   capabilities, credential references, lifecycle, health, and rollback.
2. Unknown manifest fields, embedded credentials, absolute/traversing entry
   points, and core-policy fields fail closed.
3. Capability and protocol negotiation choose only mutual supported values.
4. A universal bridge constructs protocol envelopes and invokes the shared core.
5. Adapters translate only local requests, responses, health, and rollback
   signals; they cannot replace core policy.
6. A reference adapter and copyable template work without provider calls.
7. Every SDK response preserves explicit provider accounting from the core.
8. Source hashes bind the implementation and templates used by verification.

## Threat model

- A manifest could smuggle credential values or machine-local paths.
- An adapter could claim unsupported operations or protocols.
- Adapter code could duplicate or override routing, authority, or provider policy.
- Translation could omit session, correlation, or provider-accounting evidence.
- A template could appear portable while depending on private runtime state.

## Rollback

Remove the adapter SDK row from the module manifest and remove the new module.
The Phase 71 protocol and Phase 72 shared core remain independently usable.

## Budget and activation

- Provider calls: 0
- Provider tokens: 0
- Service restarts: 0
- Runtime/profile activation impact: none
