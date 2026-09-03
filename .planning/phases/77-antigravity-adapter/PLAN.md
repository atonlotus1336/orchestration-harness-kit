# Phase 77 — Antigravity Adapter

## Goal

Evolve the existing Antigravity governed wrapper into a thin universal-protocol
adapter while preserving its fail-closed intent and leaving the live CLI
untouched.

## Dependencies and sources

- Protocol, core, adapter SDK, conformance kit, and Phase 76 core projection
- SOUND Hermes self-evolution provenance commit
- Immutable predecessor adapter, launcher, and qualification-test Git blobs

## Acceptance criteria

1. A declarative manifest and translation-only adapter cover all 31 operations.
2. Prime lane selection, budgets, accounting, safety, and token-compute policy are
   absent from the adapter and owned by the shared core.
3. The adapter inherits actual T3 routing and capability-preserving read-only
   projection in provider-free tests.
4. Provider accounting and context attribution survive local response translation.
5. Source maps bind the predecessor wrapper, launcher, and test to immutable Git
   identifiers.
6. Installer discovery is opt-in and does not change the default path.
7. Source qualification does not launch, install, modify, or activate Antigravity.

## Threat model

- Copying the PowerShell dispatcher would recreate a private orchestration core.
- Model, budget, or provider switches inside the adapter could bypass shared policy.
- A CLI probe could be mistaken for provider-free conformance.
- Historical Phase 76 evidence could be reused after aggregate source changes.

## Rollback and budget

Remove the optional adapter module and Antigravity adapter directory. The live CLI
and previously committed adapters remain unchanged.

- Provider calls and tokens: 0
- Pay-per-token and DSH calls: 0
- Service restarts: 0
- Live configuration or activation changes: 0
