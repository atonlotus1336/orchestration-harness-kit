# Universal Orchestration SDK v2 release notes

Status: local release candidate; not tagged or published.

v2 replaces per-harness orchestration copies with a shared deterministic core
and thin translation adapters. A new harness implements one small manifest and
adapter instead of rebuilding authority, routing, memory, retrieval, token
optimization, provider policy, activation, and rollback behavior.

## Highlights

- seven source-qualified adapters share one protocol and policy core
- deterministic T0–T4 routing inputs and actual-lane reporting
- progressive capability disclosure without silently hiding tools
- fixed-envelope attribution for authority, skills, tools, and selected context
- explicit provider-call and token accounting, including meaningful zeroes
- dry-run-first install, upgrade, rollback, and managed uninstall
- identical clean-room payload and ZIP bytes on Windows, Linux/WSL, and Docker
- v1 protocol negotiation and a pinned rollback point

## Boundaries

- `2.0.0-dev` remains the runtime label until release stamping is authorized.
- No profile is activated merely by installing or connecting an adapter.
- macOS is experimental.
- The Git repository is private and contains historical objects excluded from
  the public client bundle. Publish only a freshly qualified allowlisted bundle
  unless source-history remediation is separately approved.
