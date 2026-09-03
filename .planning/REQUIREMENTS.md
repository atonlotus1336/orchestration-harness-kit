# v2 Requirements

## Architecture

- **ARCH-01** A versioned, vendor-neutral orchestration protocol defines inputs,
  capabilities, lifecycle events, results, errors, and telemetry.
- **ARCH-02** Policy, routing, safety, accounting, and context selection live in a
  shared core rather than harness adapters.
- **ARCH-03** Adapters declare capabilities and degrade explicitly when a feature
  is unavailable.

## Compatibility

- **COMP-01** Existing v1 install, doctor, bundle, and uninstall behavior remains
  reproducible from a preserved baseline.
- **COMP-02** v2 upgrades are non-destructive and reversible.
- **COMP-03** Hermes OS profile state is not changed by SDK installation.

## Installation

- **INST-01** Provide deterministic install, connect, verify, upgrade, and rollback
  commands.
- **INST-02** All generated changes are recorded in a managed-file ledger.
- **INST-03** Installation is idempotent and supports dry-run output.

## Adapters

- **ADAPT-01** Ship a Hermes reference adapter.
- **ADAPT-02** Ship OpenCode and Pi adapters using the same protocol.
- **ADAPT-03** Ship an Antigravity adapter without duplicating core policy.
- **ADAPT-04** Define compatibility adapters for Codex, Claude Code, and MOA.

## Safety and efficiency

- **SAFE-01** No secrets, private identities, raw sessions, or machine evidence may
  enter public payloads.
- **SAFE-02** Provider use, service restarts, activation, and default changes are
  opt-in and separately authorized.
- **EFF-01** Prompt-envelope attribution reports system, authority, skills-index,
  and tool-schema bytes independently.
- **EFF-02** Promotion requires matched quality, provider-call count, latency,
  rollback, and token gates.

## Release

- **REL-01** Every adapter passes the provider-free conformance suite.
- **REL-02** Clean-room Windows and Linux installs pass.
- **REL-03** Release artifacts have source provenance and reproducible hashes.
- **REL-04** v1 and v2 release decisions remain truthful when a gate is blocked.
