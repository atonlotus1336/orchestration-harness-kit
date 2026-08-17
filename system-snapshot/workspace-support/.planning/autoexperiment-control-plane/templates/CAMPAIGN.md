---
schema_version: 1.0.0
campaign_id: REQUIRED
cadence: REQUIRED
backup_checkpoint_id: REQUIRED
baseline_id: REQUIRED
portfolio_id: REQUIRED
parent_experiment_id: root
hypothesis: REQUIRED
adapter_path: REQUIRED
primary_metric: REQUIRED
budget_iterations: REQUIRED
budget_no_progress_rounds: REQUIRED
budget_wall_clock_seconds: REQUIRED
state_path: REQUIRED
human_checkpoint: REQUIRED
---

# AutoExperiment Campaign

## Loop contract

LOOP CONTRACT

- trigger: REQUIRED — event, schedule, or manual
- goal: REQUIRED — one sentence describing the measurable end-state
- verifier: REQUIRED — external to the builder; deterministic whenever possible
- builder: REQUIRED — subscription-backed harness or approved local model
- judge: REQUIRED — distinct from builder for non-deterministic goals
- state: REQUIRED — append-only JSONL ledger plus campaign evidence
- hard stops: max-iter=REQUIRED; no-progress=REQUIRED rounds; wall-clock=REQUIRED seconds
- cost basis: subscription seat; no metered API
- on failure: feed error back at most twice, then stop and route to the inbox/operator
- human checkpoint: REQUIRED — before any durable or irreversible promotion

## Frozen experiment contract

- One adapter-declared editable surface and one changed file per iteration.
- One falsifiable causal change derived from the campaign hypothesis.
- Evaluator, baseline, fixtures, guardrails, and protected invariants are hashed before
  the first iteration and checked again before every decision.
- One primary metric determines improvement. Hard guardrails can only reject.
- Equivalent results prefer lower complexity, then lower measured cost.
- Every iteration appends exactly one `keep`, `discard`, or `crash` record.
- Max-iteration, no-progress, and wall-clock stops are independent and mandatory.
- Sandboxes are copy-on-write and path-confined. Git commit/reset/merge/push is absent.

## Required lineage fields

Every JSONL record contains: `experiment_id`, `campaign_id`, `cadence`,
`backup_checkpoint_id`, `baseline_id`, `portfolio_id`, `parent_experiment_id`,
`hypothesis`, `iteration`, `budget`, `primary_metric`, `baseline_metric`,
`candidate_metric`, `complexity`, `cost`, `patch_id`, `status`, `reason`,
`evaluator_sha256`, `baseline_sha256`, `rollback_id`, and `timestamp`.

## Exit clarity

Record the stop reason, best experiment, measurable gain, invariant status, total
iterations, elapsed wall-clock time, cost basis, discarded/crashed lineage, sandbox
cleanup result, and the exact evidence bundle to hand to GSD.

