# Changelog

## Unreleased

### Fixed

- Guards hook: `MultiEdit` (`file_path`) and `NotebookEdit` (`notebook_path`)
  now reach the same protected-write check as `Write`/`Edit`. Before, the
  hook ignored both tools even when the registered matcher included them.
  The README matcher example now lists both tools. 4 new replayed cases
  (67 total).

- Guards policy `1.1.0-template` + hook v3 (2026-10-15): the
  truncating-overwrite rule no longer fires on appends (`>>`), stream merges
  (`2>&1`), arrows (`->`, `=>`) or redirects into temp/scratch folders; the
  embedded fallback's docker rule no longer matches read-only commands that
  merely name the service; heredoc bodies written to plain documents and
  read-only search segments (grep/rg/findstr/Select-String) are not scanned;
  a safe prefix (`ls`, `cat`, `echo`...) no longer exempts a chained, piped
  or heredoc command. 31 new replayed cases (63 total).
- Adaptive parallel `on` and `off` now use the same recipient-adapter
  transaction and verify external state before changing paper state.
- Failed compensation reports `FAILED_ROLLED_BACK` only after exact runtime
  and paper-state restoration; uncertain restoration fails closed as
  `ROLLBACK_INCOMPLETE`.
- The operational switch CLI now loads explicit recipient adapters, returns
  nonzero when they are absent, and ships in adaptive-v2 stage, upgrade,
  rollback, and uninstall operations.

## 2.2.0

### Added

- Inactive adaptive v2 execution and staged reviewer-admission templates beside
  the unchanged v1 max-two read-only profile.
- Twelve catalog-bound career methods, symbolic temperament modifiers, and a
  closed career-Shadow result schema without Council team or voting behavior.
- Provider-free execution, reviewer, career, source-parity, staging, backup, and
  rollback checks through generic capability ports.

### Compatibility and safety

- V1 validators and historical lifecycle receipts remain unchanged.
- Installation and upgrade do not alter recipient configuration, memory,
  activation records, runtime receipts, providers, models, or services.
- The recipient's S selector and review controller remain the only owners of
  reviewer membership and authoritative completion.

## 2.1.0

### Added

- Optional, inactive-by-default additive parallel observation profile for two
  independent read-only children, parent-only completion, serial fallback, and
  provider-free feature conformance.
- Orca observation-only contract and release documentation. No Orca command,
  launch, approval, review, completion, or polling authority is shipped.

### Compatibility and safety

- The shared protocol, adapter capability manifests, default harness activation,
  provider routing, and production state remain unchanged.

### Distribution

- Added scoped proprietary terms for Kit-Proprietor-authored materials, a private
  recipient policy, and third-party license notices that preserve upstream
  rights.
- Recipient builds and release readiness now fail when required distribution
  notices are missing. Public distribution is not authorized.

## 2.0.1

### Added

- Provider-independent evidence collection and integrity enforcement for actual
  Git changes, test execution, deletion evidence, and reviewer acceptance packets.
- Governed Prime/OMP coordination dependencies, task authorization, usage
  accounting, recovery, checkpointing, and verified-success handling in the
  private system snapshot.
- Complete isolated restore dependency closure, including the Ponytail skill,
  current runtime schemas, image lock, and RPC fixture.

### Fixed

- Release qualification now derives the stable tag from the current SDK version,
  preserving older published tags instead of requiring them to move with HEAD.
- Snapshot restoration verifies the exact bytes written by the exporter across
  Windows and Linux rather than normalizing line endings before hashing.
- Native recovery resolves .NET, NuGet, temporary, and WSL paths from the host
  environment instead of fixed machine locations.

### Safety

- Automatic evaluation remains governed by the existing policy and capability
  owners; provider activation, model routing, and production state are unchanged.
- Historical qualification receipts and the published `v2.0.0` tag remain
  immutable. Version `2.0.1` requires fresh committed-source qualification.

## 2.0.0

### Added

- Shared finite-goal evaluator and CLI with evidence-bound boolean checks,
  mandatory human usability, timed goalposts, and regression protection.
- Explicit T4 autonomy intent, evidence-gated lower execution lanes, and
  independent S0-S4 review classification with guarded reviewer admission.

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

- The installer and adapter matrix use `2.0.0`. Tagging and publication require
  a verified source commit and release approval.
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
