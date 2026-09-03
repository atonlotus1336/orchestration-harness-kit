# Phase 76 — OpenCode and Pi Adapters

## Goal

Evolve the existing OpenCode and Pi request-projection integrations into thin
universal-protocol adapters, moving classification and token-compute policy into
the shared core while leaving both live harnesses unchanged.

## Dependencies

- Universal protocol, shared core, adapter SDK, and conformance kit
- Immutable Hermes self-evolution provenance commit
- Existing OpenCode and Pi request-projection source blobs

## Acceptance criteria

1. OpenCode and Pi each expose a declarative manifest and translation-only
   adapter covering all 31 operations.
2. Neither adapter imports the core or implements routing, authority, capability
   discovery, memory, retrieval, provider, activation, or optimization policy.
3. Existing projection behavior is classified as evolved: hook/extension and
   shadow-first boundaries remain, while request classification, tool selection,
   and token-compute optimization move to the shared core.
4. Both adapters preserve provider accounting and context attribution.
5. Provider-free conformance passes 62/62 combined operation paths with unchanged
   shared-core hashes.
6. Installer modules are opt-in peers and do not alter the default install path.
7. Source maps bind each predecessor to immutable commit and blob identifiers.

## Threat model

- Copying the old classifiers would fork optimization policy again.
- Adapter-local tool filtering could silently hide discovery capabilities.
- Response translation could discard cost or context evidence.
- Source qualification could be mistaken for live activation.

## Rollback and budget

Remove the two optional adapter module rows and source directories. Live OpenCode
and Pi installations remain untouched throughout.

- Provider calls and tokens: 0
- Pay-per-token and DSH calls: 0
- Service restarts: 0
- Harness configuration or activation changes: 0
