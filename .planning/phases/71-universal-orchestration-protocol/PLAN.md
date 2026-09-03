# Phase 71 — Universal Orchestration Protocol

## Goal

Define the stable v2 protocol between the shared orchestration core and thin
harness adapters.

## Dependencies

- Phase 70 version negotiation and v1 compatibility contract
- Existing messaging and Prime/OMP contracts
- Existing managed-file, privacy, and authority boundaries

## Source and output files

- Sources: messaging adapter contract, Prime/OMP contract, version policy
- Outputs: protocol specification, envelope schema, operation catalog,
  deterministic fixtures, verifier, and focused tests

## Acceptance criteria

1. The protocol covers every lifecycle, orchestration, accounting, health,
   activation, and recovery operation required by the v2 objective.
2. The catalog marks shared-core and adapter ownership explicitly.
3. Adapters own translation only and cannot declare core policy ownership.
4. Authentication carries references and never credential values.
5. Protocol negotiation supports generations 1 and 2 and fails closed.
6. Structured errors, context attribution, provider accounting, and source
   attribution have required fields.
7. Positive and negative fixtures pass the provider-free verifier.

## Threat model

- An adapter could duplicate or override core policy.
- A protocol envelope could carry a credential value.
- Missing accounting could hide provider calls or prompt growth.
- An activation request could omit approval or rollback context.
- Version drift could silently select incompatible behavior.

## Rollback

Remove the v2 protocol module and its module-manifest row. Version generation 1
and all v1 commands remain available through the Phase 70 compatibility contract.

## Budget and activation

- Provider calls: 0
- Token budget: 0 provider tokens
- Service restarts: 0
- Activation impact: none
