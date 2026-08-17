---
phase: autoexperiment-control-plane
plan: 07
status: complete-uncommitted
completed_at: 2026-08-09T16:26:00-04:00
requirements_completed: [AEC-11, AEC-12]
---

# Plan 07 Summary: Dormant Cross-Harness Skill and GSD Promotion

## Outcome

AutoExperiment now has one portable canonical skill source and one control-plane entry
point for status, scout, campaign, report, explain, and promote. The skill remains
dormant: no active manifest, junction, harness home, or global configuration changed.

Accepted winners can be rendered into a GSD implementation brief with evidence,
patch, frozen baseline/verifier, separate economics, invariants, and rollback. The
package explicitly performs no production, Git-history, or skill-activation mutation.

## Verification

| Check | Result |
|---|---|
| Harness/promotion suite | 6 passed, 0 failed |
| Total subsystem tests through Plan 07 | 78 passed, 0 failed |
| PowerShell parser | 25 files, 0 errors |
| Graph schema plus kit-component probes | GREEN, 0 RED |
| Four AutoExperiment direct probes | 4 passed |

The unrestricted graph probe encountered an unrelated machine-specific backup SSH-key
warning, so verification used full graph schema validation with the `kit-component`
class plus direct deterministic probes for the three new eval rows. Mutation rotation
was not run.

## Safety notes

- `skills-tier.ps1 apply` was deliberately not run because broad apply can change
  unrelated junction exposure.
- Plan 08 owns any targeted `autoexperiment` promotion and must capture exact
  before/after manifest and junction evidence with a rollback.
- The skill remains at `__WINDOWS_WORKSPACE__\skills-shared\autoexperiment\SKILL.md` and is not
  currently advertised to Codex, Claude Code, or Hermes.
- Surface graph rows register the dormant skill, disabled schedules, ledger, and
  control-plane entry point.

## Files

- `skills-shared/autoexperiment/SKILL.md`
- `scripts/autoexperiment/control-plane.ps1`
- `scripts/autoexperiment/promotion.ps1`
- `.planning/autoexperiment-control-plane/templates/IMPLEMENTATION-BRIEF.md`
- `SURFACE-GRAPH.yaml`
- `scripts/autoexperiment/tests/e2e.tests.ps1`
- `.planning/autoexperiment-control-plane/07-01-SUMMARY.md`
