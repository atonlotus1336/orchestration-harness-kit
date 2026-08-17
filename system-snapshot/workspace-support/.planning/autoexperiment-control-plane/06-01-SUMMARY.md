---
phase: autoexperiment-control-plane
plan: 06
status: complete-uncommitted
completed_at: 2026-08-09T16:19:00-04:00
requirements_completed: [AEC-01, AEC-08, AEC-09, AEC-10]
---

# Plan 06 Summary: Controlled Evidence and G: Disk Markdown Replay

## Outcome

AutoExperiment now ingests broad evidence through explicit source handles without
broad crawling or copying sensitive raw content. Markdown optimization is restricted
to independently registered roots on the G: disk, primarily `__WINDOWS_WORKSPACE__`; it has no
Google Drive or Google Cloud integration.

Markdown candidates win only when deterministic replay preserves required answers and
authority terms while reducing reads and estimated tokens. The optimizer is
proposal-only and does not rewrite source Markdown.

## Delivered

- Tiered, privacy-aware, provenance-preserving ingestion with local-root confinement,
  SHA-256 handles, injection quarantine, and community/CARE authorization gates.
- Registered-root Markdown inventory with canonical/authored/generated/vendor/archive
  classification and reparse-point protection.
- Retrieval replay comparing required answer terms, authority retention, read count,
  and estimated tokens.
- Ledger-reconciled weekly/monthly report generator; daily detail remains ledger-only.
- Weekly and monthly sample report formats under `__WINDOWS_WORKSPACE__\AutoExperiment-Reports`.

## Verification

| Check | Result |
|---|---|
| Ingestion suite | 5 passed, 0 failed |
| Markdown/report suite | 5 passed, 0 failed |
| Total subsystem tests through Plan 06 | 72 passed, 0 failed |
| PowerShell parser | 22 files, 0 parse errors |

Fixtures prove source attribution, no raw-content copying, registered-root
confinement, injection quarantine, privacy rejection, community-data blocking,
authority-preserving token reduction, quality-loss rejection, source hash stability,
non-G root rejection, and exact report reconciliation.

## Safety notes

- No G: disk Markdown was modified during testing or implementation.
- The optimizer cannot scan non-G roots in normal operation; fixture roots require an
  explicit test-only switch.
- Registered roots and Markdown files that are reparse points are rejected/skipped.
- No Google connector, broad vault crawl, report schedule, or production dispatch was
  created.

## Files

- `scripts/autoexperiment/ingest.ps1`
- `scripts/autoexperiment/markdown-optimizer.ps1`
- `scripts/autoexperiment/report.ps1`
- `scripts/autoexperiment/tests/ingest.tests.ps1`
- `scripts/autoexperiment/tests/markdown.tests.ps1`
- `AutoExperiment-Reports/weekly/SAMPLE.md`
- `AutoExperiment-Reports/monthly/SAMPLE.md`
- `.planning/autoexperiment-control-plane/06-01-SUMMARY.md`
