---
phase: autoexperiment-control-plane
plan: 02
status: complete-uncommitted
completed_at: 2026-08-09T15:55:00-04:00
requirements_completed: [AEC-03, AEC-04, AEC-06]
---

# Plan 02 Summary: Safe Karpathy Kernel

## Outcome

The original autoresearch method now exists as a small reusable WorkspaceKit kernel: a
frozen evaluator and baseline, one adapter-declared changed file per iteration, one
primary metric, fixed budgets, keep/discard/crash lineage, simplicity preference,
and max-iteration/no-progress/wall-clock stops.

## Delivered

- A JSON-compatible YAML adapter schema covering inspect, generate, evaluate,
  guardrail, cost, and rollback operations.
- A campaign template containing every field in the canonical WorkspaceKit Loop Contract,
  immutable experiment rules, required lineage, and exit-clarity requirements.
- A marker-attested, path-confined copy-on-write sandbox implementation that rejects
  reparse points and refuses cleanup outside its sandbox root.
- An append-only JSONL ledger validator requiring campaign, backup, baseline,
  portfolio, parent, hypothesis, budget, metric, cost, patch, status, and rollback
  lineage.
- A deterministic kernel that verifies evaluator/baseline hashes before each
  candidate, rejects undeclared or multi-file changes, prefers simpler equivalent
  results, and never invokes Git.

## Verification

| Check | Result |
|---|---|
| Plan 02 contract and kernel suite | 13 passed, 0 failed |
| Combined Plan 01–02 suite | 24 passed, 0 failed |
| PowerShell parser | 0 syntax errors across six scripts/tests |
| Improvement lineage | Two keeps; second candidate points to first as parent |
| Discard and crash behavior | Deterministic fixtures passed |
| Simplicity preference | Equivalent lower-complexity candidate kept |
| Hard stops | Max-iteration, no-progress, and wall-clock fixtures passed |
| Source isolation | Fixture source hashes unchanged after every campaign |
| Git mutation | Zero Git operations in kernel and reports |

## Safety notes

- All candidate writes occur in temporary sandbox copies.
- Cleanup resolves the absolute target, requires it to be beneath the declared
  sandbox root, and requires `.autoexperiment-sandbox.json` before recursive removal.
- Source trees containing reparse points are rejected to prevent path escape.
- Evaluator/baseline drift aborts before a candidate decision.
- The canonical experiment ledger is initialized but contains no experiment result;
  only fixture ledgers were exercised.

## Deviations

- No Git commit was created because the vault is not a valid Git worktree and shared
  rules require explicit merge-update confirmation before history changes.
- The GSD SDK remained unavailable due to its broken deleted-temp launcher; execution
  and verification were performed directly from the plan contracts.

## Files

- `.planning/autoexperiment-control-plane/adapters/schema.yaml`
- `.planning/autoexperiment-control-plane/templates/CAMPAIGN.md`
- `.planning/autoexperiment-control-plane/state/experiments.jsonl`
- `scripts/autoexperiment/kernel.ps1`
- `scripts/autoexperiment/sandbox.ps1`
- `scripts/autoexperiment/ledger.ps1`
- `scripts/autoexperiment/tests/kernel.tests.ps1`
- `.planning/autoexperiment-control-plane/02-01-SUMMARY.md`
