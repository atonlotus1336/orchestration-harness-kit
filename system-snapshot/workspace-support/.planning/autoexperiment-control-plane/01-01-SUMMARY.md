---
phase: autoexperiment-control-plane
plan: 01
status: complete-uncommitted
completed_at: 2026-08-09T15:49:00-04:00
requirements_completed: [AEC-01, AEC-05, AEC-10]
---

# Plan 01 Summary: Research Constitution and Read-Only Entry Gate

## Outcome

WorkspaceKit AutoExperiment now has a persistent research constitution, machine-readable
protected invariants and source tiers, and a read-only preflight that fails closed
before any experiment runner exists.

The live preflight result is `WARN` with 15 PASS, 1 WARN, and 0 FAIL. The warning is
expected for an environment-only check with no candidate targets declared.

## Delivered

- `RESEARCH-POLICY.md` freezes authority precedence, evidence provenance, community
  data exclusion, subscription-only routes, sandbox mutation, evaluator immutability,
  recovery eligibility, cost/benefit reporting, GSD promotion, and no automatic Git
  history mutation.
- `contracts/protected-invariants.yaml` defines eleven hard invariants, protected
  production roots, verification methods, and fail-closed outcomes.
- `contracts/source-tiers.yaml` defines authority/local/primary/secondary/community
  tiers, evidence freshness, privacy, quarantine, and required source handles.
- `scripts/autoexperiment/preflight.ps1` checks policy, contracts, canonical authority,
  surface-graph registration, evaluator, baseline, frozen fixture hashes, recovery
  freshness, ChatGPT subscription login, metered credential absence, and target safety.
- `scripts/autoexperiment/tests/preflight.tests.ps1` covers policy parsing, negative
  fixtures, valid-environment behavior, read-only hashing, and missing authority,
  recovery, evaluator, or authorization.

## Verification

| Check | Result |
|---|---|
| Policy-tagged Pester suite | 5 passed, 0 failed |
| Full Pester suite | 11 passed, 0 failed |
| Live `preflight.ps1 -Mode Check -Json` | 15 PASS, 1 expected WARN, 0 FAIL |
| PowerShell parser | 0 syntax errors in both scripts |
| Read-only fixture proof | All fixture hashes identical before and after |

## Safety notes

- The preflight parses the surface graph directly and does not invoke `graph-gate.ps1`,
  because that gate writes reports and mutation-rotation state.
- External graph content is parsed as data; no graph probe command is executed.
- Protected target overlap fails unless the caller declares an isolated sandbox.
- When targets are declared, inability to establish dirty-worktree overlap fails closed.
- No production configuration, backup task, evaluator, baseline, fixture, authority
  document, skill junction, or Git history was changed.

## Deviations

- The installed GSD SDK launcher points to a deleted temporary installer location, so
  SDK state commands were unavailable.
- `__WINDOWS_WORKSPACE__\.git` is not a valid repository. No repository was initialized and no
  commit was created. All work remains uncommitted in accordance with the shared
  confirmation rule.

## Files

- `.planning/autoexperiment-control-plane/RESEARCH-POLICY.md`
- `.planning/autoexperiment-control-plane/contracts/protected-invariants.yaml`
- `.planning/autoexperiment-control-plane/contracts/source-tiers.yaml`
- `scripts/autoexperiment/preflight.ps1`
- `scripts/autoexperiment/tests/preflight.tests.ps1`
- `.planning/autoexperiment-control-plane/01-01-SUMMARY.md`
