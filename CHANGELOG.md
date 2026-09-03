# Changelog

## 2.0.0 release candidate — not published

### Added

- A versioned universal orchestration protocol and one deterministic shared core.
- A thin-adapter SDK plus reference adapters for Hermes OS, OpenCode, Pi,
  Antigravity, Codex, Claude Code, MOA, and Cursor.
- Provider-free conformance, fixed-envelope attribution, call accounting, and
  fail-closed activation/rollback contracts.
- Dry-run-first install, connect, doctor, verify, upgrade, rollback, uninstall,
  and deterministic offline-bundle lifecycle.
- Native Windows and Linux/WSL clean-room qualification with identical payload
  and archive bytes.
- A provider-free, rollback-backed token-compute activation module shared by all
  eight adapters, plus fail-closed Cursor prompt admission.

### Compatibility and safety

- v1 remains an immutable rollback point and protocol generation 1 remains
  negotiable.
- Major upgrades are never automatic; recipient data, credentials, and unmanaged
  files remain recipient-owned.
- Source qualification does not activate any harness, provider, model, service,
  or production profile.

### Publication state

- The runtime version remains `2.0.0-dev` until a separately authorized release
  stamp, tag, and publication action.
- The GitHub repository is currently private. The public client bundle remains
  the supported disclosure boundary because historical Git objects are not a
  public-release surface.

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
- Prime/OMP diagnostics that distinguish MCP connectivity, stale session tool
  snapshots, and canonical OMP runtime readiness.
- Advisory receipts for optional Prime delegation and Prime-governed OMP use.
- A dry-run-first initializer for the gitignored maintainer privacy policies
  required by the pre-commit tripwire.
- A clean-recipient Prime/OMP restore and Hermes-overlay acceptance gate, plus
  documentation separating the public contract-only payload from the full-Git
  bridge restoration path.

### Fixed

- Replaced the sanitized fixed WSL distribution label with a required
  `__WSL_DISTRO__` restore placeholder so recipient containment policies bind
  to the actual client distribution.

- Prevented Windows callers from producing false OMP runtime failures by
  dispatching the feasibility probe into the configured WSL runtime.
- Prevented `tool_search` and the immutable OMP runtime lock from being used as
  live availability authorities.

### Security and snapshot boundary

- The private repository now preserves real implementation code and operating
  policy while excluding credentials, `.env` files, caches, runtime ledgers,
  generated reports, machine evidence, memories, projects, and backup contents.
