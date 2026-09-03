# Universal Orchestration SDK v2

## Objective

Evolve the published orchestration harness kit into a portable, modular SDK that
preserves the v1 contract while exposing a shared orchestration core through thin,
testable harness adapters.

## Product principles

- Preserve v1 as an immutable compatibility baseline.
- Keep orchestration policy in one vendor-neutral core.
- Make harness integrations thin: translate requests, capabilities, events, and results.
- Deny unsafe actions by default and preserve subscription-first routing.
- Measure token-envelope changes separately from task-output quality.
- Require provider-free conformance before any bounded live qualification.
- Never embed machine identity, credentials, sessions, or private evidence.

## Protected surfaces

Hermes OS Home, Workspace, NucBox Telegram, and VPS Telegram are protected
production profiles. SDK development must not restart, reconfigure, or activate
them. Existing review-only or production state is evidence, not permission.

## Definition of done

v2 is complete only when the shared protocol, core, adapter SDK, reference
adapters, installer lifecycle, conformance suite, clean-room evaluation, privacy
gates, rollback path, and release documentation all pass from clean worktrees.
