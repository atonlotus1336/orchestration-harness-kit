---
initiative: autoexperiment-control-plane
status: locked-for-planning
created: 2026-08-09
source: user-design-conversation
---

# AutoExperiment Control Plane - Context

## Outcome

Build a WorkspaceKit-wide improvement system that repeatedly discovers, ranks, tests, and reports worthwhile improvements across the orchestration ecosystem. It must preserve working behavior, compare cost with benefit, distribute attention across every important category, and turn proven winners into ordinary GSD implementation work.

## Decisions

- This is a control-plane subsystem with a thin cross-harness skill, not a standalone prompt and not a copy of the upstream training repository.
- The Karpathy autoresearch method is retained as a small experiment kernel: frozen evaluator, one editable surface, one causal change, fixed budget, measured result, keep/discard/crash, and a simplicity preference.
- The system is nested rather than linear. Each category runs its own bounded mini-loop with entry, verification, clarity, and exit gates. Cross-category convergence may send work back to affected categories.
- Every category receives minimum scheduled coverage. Ranking changes depth and order, not whether a category is examined at all.
- The rank order is lexicographic: invariant failure; verified degradation; coverage debt; downstream leverage; frequency/user impact; experiment readiness; cost and duration. An aging bonus prevents starvation.
- A candidate cannot be a winner if it breaks an existing protected behavior. Whole-system regression, compatibility, rollback, and cost-benefit checks are part of evaluation.
- Daily cycles are small and recovery-bounded. Weekly and monthly cycles test broader changes and produce Markdown reports.
- The canonical report root is `__WINDOWS_WORKSPACE__/AutoExperiment-Reports/`, with `weekly/` and `monthly/` subfolders. Daily activity is ledger-only unless an alert is warranted.
- Backup success creates an eligible research checkpoint; backup failure blocks that cycle. A backup is a recovery anchor, not evidence that a candidate is good.
- Every cadence chain starts its backup at 11:59 AM. Its follow-up improvement loop is event-driven and begins immediately after that backup and restore verification succeed, avoiding a same-minute race. The weekly chain runs Saturday; the monthly-due chain runs the following Monday after the weekly sequence.
- Markdown/token optimization is a first-class portfolio, using registered-root scans and retrieval replay rather than a whole-drive crawl.
- Email or messaging is a delivery surface only. Local Markdown and structured ledgers remain canonical.
- MemoryWeaver receives adopted durable conclusions, not raw experiment chatter.
- Community/Indigenous data is unavailable to the system unless the relevant community authorization and CARE-aligned consent are recorded. Personal approval alone cannot authorize community data use.
- Research uses subscription-backed harnesses and approved local models. No metered API route is introduced.
- The control plane proposes evidence packages and GSD work. It does not directly merge, push, publish, rewrite authority, or silently promote durable changes.

## Portfolios

1. Knowledge and retrieval
2. Agent and prompt quality
3. Engineering delivery
4. Reliability and recovery
5. Safety, privacy, and governance
6. Learning and memory
7. Product and mission outcomes
8. Efficiency and operational cost
9. Markdown and token efficiency

## Cadence Intent

| Cadence | Scope | Typical campaign budget | Canonical output |
|---|---|---:|---|
| Daily | After the daily 11:59 AM backup verifies; edge cases and low-risk micro-improvements on backup-covered surfaces | 1-3 campaigns, at most 5 iterations each, at most 60 minutes total | JSONL ledger; alert only when material |
| Weekly | After the Saturday 11:59 AM Projects backup verifies; cross-layer workflows and the highest-ranked needs, while still touching every portfolio | 3-5 campaigns, 10-20 iterations each, 3-6 hours total | Weekly Markdown report and candidate stack |
| Monthly | After the Monday 11:59 AM monthly-due backup verifies; portfolio-wide synthesis, architecture simplification, and current external research | 5-10 deep campaigns, 24-hour hard ceiling | Monthly Markdown report and next-month agenda |

## Success Definition

The subsystem is successful when it can run a bounded pilot from a verified backup checkpoint, give every portfolio coverage, produce reproducible candidate evidence, reject regressions and uneconomic complexity, generate clear weekly/monthly reports, and hand a proven winner to GSD without altering production during research.
