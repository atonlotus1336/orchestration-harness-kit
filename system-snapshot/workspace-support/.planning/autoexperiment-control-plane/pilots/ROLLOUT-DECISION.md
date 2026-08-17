# AutoExperiment Rollout Decision

- Decision: automatic report-only observation after verified backups
- Approved by: Jonathan
- Recorded: 2026-08-09
- Skill visibility: scheduled report-only
- AutoExperiment schedules: daily, weekly, and monthly post-backup observation tasks authorized
- Backup schedules and entry points: unchanged
- Production mutation: not authorized
- Git history mutation: not authorized

## Operating boundary

AutoExperiment may run automatic read-only scans and generate reports only after a
successful backup and green restore verification. It may not promote the shared skill
into mutation-capable harness tiers or implement candidate changes directly.

Candidate implementation, promotion, Git mutation, and production configuration changes
remain human-controlled. Observation records time-to-signal, false positives,
reproducibility, rate-limit use, accepted-benefit ratio, and independent critique.
