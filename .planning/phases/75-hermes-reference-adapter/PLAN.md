# Phase 75 — Hermes OS Reference Adapter

## Goal

Represent Hermes OS as the canonical thin-adapter implementation for Home,
Workspace, NucBox Telegram, and VPS Telegram while leaving all live runtimes and
profiles unchanged.

## Acceptance criteria

1. One declarative manifest negotiates the complete v2 operation catalog.
2. The adapter translates only Hermes events, responses, readiness, and rollback
   signals; it neither imports nor duplicates the shared core.
3. All four protected Hermes surfaces are explicitly represented as
   source-qualified and runtime-unchanged.
4. Provider accounting and context attribution survive response translation.
5. The adapter passes all 31 provider-free conformance cases with unchanged core
   hashes.
6. Immutable SOUND provenance binds the reference contract to its three upstream
   source repositories.
7. Installer discovery is opt-in and does not alter the universal default path.

## Non-goals and hard boundaries

- No profile migration, service restart, provider/model/default change, or live
  activation.
- No endpoints, machine paths, credentials, sessions, or private evidence.
- Source qualification does not claim runtime qualification.

## Rollback

Remove the optional `adapter-hermes` module entry and this adapter directory. The
protocol, core, SDK, conformance kit, v1 compatibility, and live Hermes profiles
remain unchanged.

## Budget

- Provider calls and tokens: 0
- Pay-per-token and DSH calls: 0
- Service restarts: 0
- Activation changes: 0
