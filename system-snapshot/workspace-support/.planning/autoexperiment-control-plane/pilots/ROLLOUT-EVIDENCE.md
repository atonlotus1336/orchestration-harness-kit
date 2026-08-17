# AutoExperiment Rollout Evidence

## Recommendation

Keep AutoExperiment **manual and report-only** for now. The control plane mechanics are
strongly verified, but fixture pilots are not enough evidence to enable recurring
background schedules or cross-harness skill visibility.

## What the pilots proved

- Retrieval ranking shows every one of the nine portfolios before surplus depth.
- Markdown replay distinguishes a smaller good candidate from a smaller candidate that
  loses authority or answer quality.
- The cross-layer safety pilot accepts a reproducible Pareto improvement and rejects a
  deliberately bad regression/uneconomic candidate.
- Copy-on-write cleanup and ablation preserve source hashes and demonstrate rollback.
- All pilots were local deterministic fixtures with zero subscription rate-limit use
  and zero production changes.

Machine-readable evidence: `PILOT-RESULTS.json`.

## Current gates

| Gate | Result |
|---|---|
| Subsystem tests | 78 passed, 0 failed |
| Portfolio coverage | 9/9 represented |
| Good/bad distinction | Passed |
| Rollback/source preservation | Passed |
| Existing backup entry points | Byte-identical |
| AutoExperiment scheduled tasks | 0 installed/enabled |
| Skill visibility | Dormant; no active manifest/junction change |
| Graph schema and new skill probe | GREEN |
| Full graph all-mode | Not accepted as evidence: unrelated backup SSH-key warning on this machine |
| Sustained observation | Missing |
| Independent adversarial critique | Missing |

## Operational diff

| Surface | Before | After |
|---|---|---|
| Backup scripts/tasks | Existing working configuration | Unchanged |
| Production orchestration | Existing working configuration | Unchanged |
| AutoExperiment schedules | None | Disabled definitions only; no Windows task |
| Cross-harness skill | None | One dormant source; not advertised |
| Research state | None | Empty append-only ledger and fixture evidence |
| Reports | None | Sample weekly/monthly formats only |

## Costs and limitations

The deterministic fixtures used no model rate-limit budget. Real daily/weekly/monthly
cost, false-positive rate, time-to-signal, and accepted-benefit ratio remain unknown
until a bounded manual observation window runs. The fixture accepted-benefit ratio
(1/2) is a mechanism check, not a forecast.

## Aggregate rollout choices

1. **Manual/report-only (recommended now):** keep schedules disabled and the skill
   dormant; run a bounded manual weekly observation, then revisit this record.
2. **Bounded activation later:** targeted promotion of only `autoexperiment`, followed
   by disabled-task creation and an explicit before/after junction/task diff. Enable
   only after independent critique and observation evidence.

## Human rollout decision

Status: **APPROVED — MANUAL/REPORT-ONLY OBSERVATION**

Recorded: 2026-08-09

The operator approved the recommended manual/report-only observation mode. Schedules
remain disabled, the shared skill remains dormant, and no task registration,
production mutation, or active-tier promotion is authorized by this decision.

Manual observation runs are operator-triggered and continue to use verified recovery
checkpoints, bounded sandboxes, ledger reconciliation, and weekly/monthly report
formats. A later move to bounded activation requires a new aggregate rollout decision
based on sustained observation and independent critique evidence.
