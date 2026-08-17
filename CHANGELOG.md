# Changelog

## Unreleased — patched ecosystem release

### Added

- Optional Prime Agent and oh-my-pi integration contract with disposable
  worktrees, patch-only output, and Hermes-controlled verification.
- Optional daily, weekly, and monthly maintenance contract gated by successful
  backups, sandbox-only analysis, required evidence, and human confirmation.
- Deterministic self-tests for both new policy modules.
- Solo-PC default topology with disabled optional VPS and second-PC profiles.
- Direct Telegram adapter, sender allowlist, setup guide, and failure map.
- Small-model cold-start instructions and an honest local 8B reference result.
- Checksum-bound Prime/OMP preparation and a backup-gated maintenance runner.
- Private 311-file implementation snapshot covering the real Prime Agent bridge,
  Oh My Pi worker runtime, Hermes Telegram tooling, auto-learning, autoexperiment,
  verified maintenance, and backup/restore operations.
- Deterministic snapshot exporter, SHA-256 manifest, and dry-run-first restore tool.

### Security and snapshot boundary

- The private repository now preserves real implementation code and operating
  policy while excluding credentials, `.env` files, caches, runtime ledgers,
  generated reports, machine evidence, memories, projects, and backup contents.
