# v2 Provenance Audit

## Decision

The existing repository is the v2 evolution base. Its remote default branch is the
published v1 candidate, while this release branch carries two compatible local
changes that must enter through an ordinary merge. History must not be rewritten.

The remote v1 candidate is functionally reproducible but is not yet eligible for a
verified v1 tag: provider-free tests pass, while the privacy gate reports inherited
findings that require classification and regression tests in Phase 70. This HOLD is
historical truth, not a reason to discard v1.

## Selective-import policy

Implement or selectively import Phase 49–68 improvements only from
the immutable source commits in `provenance-map.json`. A dirty working tree, an
untracked receipt, or a stale source hash is not acceptable provenance.

Eligible concepts include deterministic routing, progressive context disclosure,
prompt-envelope attribution, named-provider locking, provider-call accounting,
review projection compatibility, bounded promotion contracts, and rollback gates.
Harness-specific machine operations and activation receipts remain evidence.
They are not portable SDK implementation.

## Evidence classes

- **SOUND** — immutable source commit, matching verification, and portable scope.
- **HISTORICAL** — truthful HOLD, failure, rollback, or non-promotion evidence.
- **WEAK** — stale hash, incomplete verification, or ambiguous ownership.
- **EXCLUDE** — secrets, machine evidence, sessions, caches, binaries, fixtures,
  temporary phase directories, backups, or unrelated changes.

## Activation impact

This audit changes no provider, model, profile, service, shadow route, review route,
or production state. It performs no provider calls.
