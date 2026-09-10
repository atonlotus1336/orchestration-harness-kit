# Universal Orchestration SDK v2.0.1 release notes

Version: 2.0.1. Check GitHub for publication status.

This patch release adds the provider-independent evidence and governed worker
coordination improvements to the portable private kit without replacing its
existing T0-T4 execution router, S0-S4 review depth, capability owner, approval
owner, or state owner.

## Highlights

- eight source-qualified adapters continue to share one protocol and policy core
- deterministic evidence captures actual Git changes and executed test results
- deletion reviews carry before-state and diff evidence
- reviewer packets bind objectives, constraints, acceptance criteria, and change evidence
- capability and Git-filter refusal occurs before Git observation paths
- unresolved blocking findings cannot be cleared by an unrelated run
- Prime/OMP job coordination includes authorization, usage accounting,
  single-flight control, recovery, checkpoints, and verified-success handling
- private snapshot restoration includes the complete isolated-test dependency closure

## Boundaries

- Installing or connecting an adapter does not activate a profile.
- Automatic evaluation remains subject to the existing policy owner.
- Provider calls, provider tokens, service restarts, and runtime activation remain
  unchanged during qualification.
- The published `v2.0.0` tag is immutable. This release uses `v2.0.1` and requires
  fresh Windows and Linux receipts generated from the committed release source.
- The Git repository remains private. Publish only a freshly qualified allowlisted
  bundle unless source-history remediation is separately approved.

## Compatibility

Protocol generation 1 remains negotiable and the pinned v1 commit remains the
rollback baseline. The changes are additive and preserve the existing lifecycle,
authorization, approval, routing, review, and rollback contracts.
