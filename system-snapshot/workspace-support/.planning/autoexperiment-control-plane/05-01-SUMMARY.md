---
phase: autoexperiment-control-plane
plan: 05
status: complete-uncommitted
completed_at: 2026-08-09T16:12:00-04:00
requirements_completed: [AEC-07, AEC-08]
---

# Plan 05 Summary: Recovery Checkpoints and Disabled Cadence

## Outcome

Verified recovery evidence is now the sole eligibility boundary for a research
dispatch. Daily, weekly, and monthly cadence definitions reference the existing
11:59 AM backup chains but have no independent clock trigger and remain disabled
until Plan 08.

Existing backup scripts and registered backup behavior were not edited. Integration
uses an external post-success checkpoint record, so a failure in AutoExperiment cannot
weaken or replace the backup path.

## Delivered

- Idempotent checkpoint adapter recording backup ID/kind/status/time, manifest path
  and hash, included/excluded surfaces, restore result, and bounded eligible scope.
- Fail-closed handling for failed, partial, stale, future, mismatched, unverified, or
  out-of-scope evidence.
- Disabled schedule contract for daily, Saturday weekly, and following-Monday monthly
  chains, all referencing 11:59 AM backup starts.
- Event-only dry-run scheduler with daily/weekly/monthly budgets, nine-way weekly
  coverage, a 24-hour monthly ceiling, and an independent kill switch.

## Verification

| Check | Result |
|---|---|
| Checkpoint suite | 6 passed, 0 failed |
| Cadence suite | 5 passed, 0 failed |
| Total subsystem tests through Plan 05 | 62 passed, 0 failed |
| AutoExperiment Windows tasks | 0 present, 0 enabled |
| AutoExperiment PowerShell parser | 17 files, 0 parse errors |

Regression fixtures hash all three existing backup entry points before and after
checkpoint/scheduler execution and prove they remain byte-identical.

## Existing backup hashes

- `register-vps-backup-tasks.ps1`: `69E08DEF956C942BD9260F0E6AF31851EDFBD0AAF8EA0173BCEC84442D8550D4`
- `backup-vault.ps1`: `763CBB84E34EF1A325387C49ECC809DDCDB6A5719140509B451BF385747D26F9`
- `backup-projects.ps1`: `B9E14D164E9D4E162069F1F409AC8C1322E6ACFDA704A43C5C33FBD479883E37`

## Safety notes

- A successful backup alone is insufficient; restore verification, manifest hash,
  freshness, and scope containment must all pass.
- Duplicate backup IDs do not append another checkpoint or dispatch another cycle.
- The kill switch blocks research only and does not alter backup tasks.
- Schedule evaluation emits a dry-run plan; it cannot register or enable a task.
- No backup script, backup task, production configuration, or Git history changed.

## Deviations

- Plan 05 was tightened to use an external adapter and disabled schedule contract
  instead of modifying backup entry points or their registrar.
- Work remains uncommitted under the shared history confirmation rule.

## Files

- `.planning/autoexperiment-control-plane/05-01-PLAN.md`
- `.planning/autoexperiment-control-plane/contracts/schedules.yaml`
- `scripts/autoexperiment/checkpoint.ps1`
- `scripts/autoexperiment/scheduler.ps1`
- `scripts/autoexperiment/tests/checkpoint.tests.ps1`
- `.planning/autoexperiment-control-plane/05-01-SUMMARY.md`
