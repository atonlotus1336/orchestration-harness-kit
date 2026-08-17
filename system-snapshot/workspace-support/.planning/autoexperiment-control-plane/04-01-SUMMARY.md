---
phase: autoexperiment-control-plane
plan: 04
status: complete-uncommitted
completed_at: 2026-08-09T16:07:00-04:00
requirements_completed: [AEC-05, AEC-06]
---

# Plan 04 Summary: Compatibility, Economics, and Safe Ablation

## Outcome

AutoExperiment can no longer accept a candidate merely because one local metric
improved. The judge keeps benefit, cost, uncertainty, complexity, compatibility, and
protected invariants separately inspectable. Coupled candidates require causal
ablation evidence before they can become promotable.

The ablation ambiguity was closed by amending the plan and implementation: a
component removal is a reversible rename inside a fresh marker-attested sandbox copy.
The source sandbox and every production path remain untouched.

## Delivered

- Boolean-gated cost model with measured factors and no synthetic scalar score.
- Judge that rejects missing/failed invariants, whole-system regression, and
  unjustified cost increases; leaves noisy or unreproduced gains unproven; and accepts
  reproducible Pareto or strict-simplification wins.
- Ablation runner with declared-root confinement, sandbox marker validation,
  traversal/rooted-path rejection, frozen evaluator hashing, component budgets,
  reversible component renames, source before/after hashing, and causal attribution.
- Unresolved multi-component interactions return `unresolved-interaction` and remain
  non-promotable for category convergence.

## Verification

| Check | Result |
|---|---|
| Judge suite | 7 passed, 0 failed |
| Ablation suite | 7 passed, 0 failed |
| Total subsystem tests through Plan 04 | 51 passed, 0 failed |
| PowerShell parser | 0 errors across all 14 scripts/tests |
| Live preflight | 15 PASS, 1 expected undeclared-target WARN, 0 FAIL |

Ablation fixtures prove attribution, unresolved interaction, unchanged source hashes,
marker enforcement, traversal rejection, budget enforcement, evaluator-drift
rejection, and sandbox-root enforcement.

## Safety notes

- No component is deleted from the source sandbox or production.
- Recursive cleanup remains centralized in `Remove-AutoExperimentSandbox`, which
  requires both root confinement and a sandbox marker.
- A component path cannot be rooted, contain `..`, or resolve outside the disposable
  copy.
- The evaluator hash is verified before any ablation copy is created.
- No backup, scheduled task, harness configuration, skill exposure, production file,
  or Git history was changed.

## Deviations

- Plan 04's ablation action was tightened before implementation to state the sandbox
  and source-integrity requirements explicitly.
- Work remains uncommitted because the vault is not a valid Git worktree and shared
  rules require explicit confirmation before history changes.

## Files

- `.planning/autoexperiment-control-plane/04-01-PLAN.md`
- `.planning/autoexperiment-control-plane/contracts/cost-model.yaml`
- `scripts/autoexperiment/judge.ps1`
- `scripts/autoexperiment/ablation.ps1`
- `scripts/autoexperiment/tests/judge.tests.ps1`
- `.planning/autoexperiment-control-plane/04-01-SUMMARY.md`
