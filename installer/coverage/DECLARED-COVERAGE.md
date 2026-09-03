# M11 Declared Coverage

> Historical note: this section describes the earlier M11 installer. Current v2
> coverage is stated first so the old checklist cannot be mistaken for the
> release-candidate boundary.

## Universal SDK v2 current coverage

- Protocol generations 1 and 2 negotiate deterministically.
- One shared orchestration core owns authority, routing, retrieval, memory,
  capability discovery, token attribution, provider accounting, activation,
  and rollback policy.
- Thin adapters are source-qualified for Hermes OS, OpenCode, Pi, Antigravity,
  Codex, Claude Code, and MOA.
- Install, connect, doctor, verify, upgrade, rollback, and uninstall pass without
  a provider call.
- The same allowlisted payload and offline bundle are byte-identical on native
  Windows, Linux/WSL, and the qualified Docker Linux container.
- Recipient data and unmanaged files survive upgrade, rollback, and uninstall.
- Live harness activation, macOS, messenger delivery, and provider-backed quality
  canaries remain separate deployment qualifications.

## Automated Locally

- Windows preflight script syntax and required tool detection.
- Linux/WSL preflight script syntax.
- Module manifest shape.
- Solo-PC topology default, single storage authority, loopback dashboard, and
  authenticated remote-role contracts.
- Telegram adapter sender-denial self-test (no live token is exercised).
- Prime/OMP provenance preparation and backup-gated maintenance self-tests.
- Private system snapshot file hashes, source boundary, and restore dry-run.
- Hermes snapshot provenance and checksums.
- Clone-eval public subset contains only `code-fix` and `agentic-file-op`.
- Privacy scrub over the M11 kit surface.

## Requires Human-Provided Target

- Clean VM/container full install.
- Full module execution in a fresh environment.
- Recipient clone-eval within 10 points of shipped reference.
- Live Telegram send/receive using recipient credentials.
- Remote-side VPS or second-PC reachability and firewall checks.

## Manual Matrix

- Windows native: declared checklist, not auto-proved here.
- macOS: declared checklist, not auto-proved here.
- Linux/WSL/container: target required by PLAN human task M11.

## Mission-restore (Plan 01 Task 8.3a, 2026-07-08)
The `domain` kit module (capability slot `missions`) ships a mission's portable MISSION.md
(statement, horizons, metric DEFINITIONS, backlog), its eval pack, routing/council rows, and
M15 seed pack — EXCEPT community-mission, which ships structure/templates ONLY (never the
consent register or any community-linked row; I2-enforced). Clone-parity extends to
mission-restore: a recipient enabling the `missions` slot receives the portable mission
scaffold and can verify it installed, WITHOUT any private community data crossing the export.
