# Universal Orchestration SDK v2 release notes

Status: local release candidate. Maintainers have not tagged or published it.

v2 replaces per-harness orchestration copies with a shared deterministic core
and thin translation adapters. A new harness implements one small manifest and
adapter instead of rebuilding authority, routing, memory, retrieval, token
optimization, provider policy, activation, and rollback behavior.

## Highlights

- eight source-qualified adapters share one protocol and policy core
- deterministic T0–T4 routing inputs and actual-lane reporting
- progressive capability disclosure without silently hiding tools
- fixed-envelope attribution for authority, skills, tools, and selected context
- explicit provider-call and token accounting, including meaningful zeroes
- dry-run-first install, upgrade, rollback, and managed uninstall
- identical clean-room payload and ZIP bytes on Windows, Linux/WSL, and Docker
- v1 protocol negotiation and a pinned rollback point

## Boundaries

- `2.0.0-dev` remains the runtime label until the owner authorizes release stamping.
- Installing or connecting an adapter does not activate a profile.
- macOS is experimental.
- The Git repository is private and contains historical objects excluded from
  the public client bundle. Publish only a freshly qualified allowlisted bundle
  unless source-history remediation is separately approved.
