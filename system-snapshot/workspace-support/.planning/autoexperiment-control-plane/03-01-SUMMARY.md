---
phase: autoexperiment-control-plane
plan: 03
status: complete-uncommitted
completed_at: 2026-08-09T16:03:00-04:00
requirements_completed: [AEC-02, AEC-04]
---

# Plan 03 Summary: Balanced Portfolios and Nested Mini-Loops

## Outcome

AutoExperiment now guarantees visible coverage for all nine due portfolios before
allocating any surplus experiment depth. Surplus follows a deterministic,
evidence-first lexicographic rank with aging, so high-need work receives more effort
without allowing one bottleneck to hide the rest of the ecosystem.

Category campaigns now use gated mini-loops with return edges rather than a linear
pipeline. Failed clarity, reproduction, falsification, verification, integration, or
exit clarity routes back to the appropriate earlier state within hard bounds.

## Delivered

- Nine-portfolio contract covering knowledge/retrieval, agent/prompt quality,
  engineering delivery, reliability/recovery, safety/privacy/governance,
  learning/memory, product/mission outcomes, efficiency/cost, and Markdown/tokens.
- Coverage-first ranker with invariant failure, verified degradation, coverage debt,
  aging, downstream leverage, recurrence/user impact, verifier readiness,
  benefit/cost, duration, and stable-ID tie-breaking.
- Scoped scout that accepts only registered portfolios with attributable evidence
  handles and explicitly records that no broad scan occurred.
- Category state machine for entry, clarity, reproduction, verifier checkpoint,
  falsification, kernel, independent verification, integration, and exit clarity.
- Convergence routing that returns cross-category effects to affected clarity loops
  and blocks coupled packages until ablation is recorded.

## Verification

| Check | Result |
|---|---|
| Ranking suite | 7 passed, 0 failed |
| Nested-loop suite | 6 passed, 0 failed |
| Prior Plan 01–02 suites | 24 passed, 0 failed |
| Total subsystem tests | 37 passed, 0 failed |
| PowerShell parser | 0 syntax errors across all 11 scripts/tests |

The fixtures prove nine-way coverage before surplus, priority-sensitive depth,
aging, deterministic ties, insufficient-budget refusal, scoped evidence, full gated
completion, verifier/context separation, frozen-contract drift detection,
no-progress stopping, ablation blocking, and cross-category feedback edges.

## Safety notes

- Plan 03 performs no production mutation, Git operation, backup edit, task
  registration, skill exposure, or external call.
- Campaign state is written only to its caller-declared state path.
- Frozen contract hashes are checked at entry and before every state transition.
- Coupled changes are not promotable without ablation evidence.

## Deviations

- No Git commit was created; the vault is not a valid Git worktree and the shared
  history confirmation rule remains controlling.
- GSD SDK state commands remain unavailable because the installed launcher targets a
  deleted temporary installer directory.

## Files

- `.planning/autoexperiment-control-plane/contracts/portfolios.yaml`
- `.planning/autoexperiment-control-plane/contracts/ranking.yaml`
- `scripts/autoexperiment/scout.ps1`
- `scripts/autoexperiment/rank.ps1`
- `scripts/autoexperiment/campaign.ps1`
- `scripts/autoexperiment/converge.ps1`
- `scripts/autoexperiment/tests/scheduler.tests.ps1`
- `.planning/autoexperiment-control-plane/03-01-SUMMARY.md`
