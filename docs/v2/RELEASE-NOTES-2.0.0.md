# Universal Orchestration SDK v2 release notes

Version: 2.0.0. Check GitHub for publication status.

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
- one rollback-backed token-compute feature profile for all eight adapters:
  budget/cache/rendering/accounting controls activate provider-free, uncertain
  depth retains full execution, and marginal-value stopping stays shadow-only
- dry-run-first install, upgrade, rollback, and managed uninstall
- reproducible payload and ZIP builds, with Windows and Linux/WSL release qualification
- v1 protocol negotiation and a pinned rollback point

## Boundaries

- The installer and adapter matrix use the stable version `2.0.0`.
- Installing or connecting an adapter does not activate a profile.
- macOS is experimental.
- The Git repository is private and contains historical objects excluded from
  the public client bundle. Publish only a freshly qualified allowlisted bundle
  unless source-history remediation is separately approved.

## Local goalpost and routing integration

The shared core now packages the finite goalpost evaluator and CLI, mandatory
human-usability criteria, regression protection, and bounded work accounting.
Execution routing reserves T4 for explicit autonomous intent and explains the
actual gated lane. Review selection uses separate S0-S4 scope and risk floors,
with no reviewer calls during classification. See GOALS-AND-ROUTING.md for host integration boundaries.

## DeepSeek Harness (DSH)

The author installed and exercised official DeepSeek Harness separately. It is not a ninth adapter in this portable release. The kit ships
shared orchestration capabilities and eight reference adapters. It does not
bundle DSH or claim that DSH connects to this core. A DeepSeek model route
inside another harness does not establish a DSH harness integration.
