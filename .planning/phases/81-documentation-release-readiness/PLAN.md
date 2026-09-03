# Phase 81 — Documentation and Release Readiness

## Goal

Finish the universal SDK as one locally releasable, provider-free candidate with
an under-ten-minute quick start, migration and authoring guides, an honest
compatibility matrix, release notes, and a deterministic release gate.

## Coding route

The governed evaluator classified the three declared Phase 81 paths as actual
lane T3. Execution was intentionally continued with native tools because the
goal forbids provider calls without separate authorization.

## Acceptance criteria

1. All public v2 documents exist and link to current source authorities.
2. Windows and Linux/WSL claims match Phase 80 evidence, Docker receives a fresh
   clean-container receipt, and macOS remains experimental.
3. Public-payload privacy remains zero findings.
4. Historical Git objects, commit metadata, and repository visibility are
   reported separately from the clean client artifact.
5. The runtime remains `2.0.0-dev`; tag, push, publication, and visibility do not
   change.
6. A clean exact-commit rerun reproduces the cross-platform artifact after these
   public-document changes.

## Finish line

Phase 81 has one implementation checkpoint and one exact-commit evidence bind.
After that, Phase 82 records the activation boundary and ends at an explicit
authorization hold. There is no Phase 83.

## Safety accounting

- provider calls and tokens: 0
- pay-per-token and DSH calls: 0
- service restarts: 0
- live activation, provider, model, and default changes: 0
- tags, pushes, releases, and repository-visibility changes: 0
