# Herdr evaluator private-kit packaging summary

## Outcome

The private kit now contains the committed Herdr evaluator layer, its portable reviewer dependencies, and a permanent checksum-pinned Herdr 0.9.0 installer. Restores remain inactive by default. After restoration, one management script configures Windows, activates Codex in supervised mode, reports status, deactivates, or atomically rolls back without rebuilding Herdr.

The integration payload is based on source commit `cb289bf5755f79729c87b17039bf434cb22b84da`; the universal nine-harness switch correction is synchronized from `7bb86808c00e853e522a3ffaf4aa886857324141`. The upstream release tag resolves to commit `b99002ac99b09e00b4ca692436cb15a6b0d676f1`.

## Safety boundary

- No Herdr binary, upstream checkout, credential, reviewer session, local state, or live evidence is stored in Git.
- The installer defaults to dry-run and requires both `--apply` and `--yes` to mutate.
- Portable defaults are off and non-runnable across Codex, Claude Code, Hermes, MOA, Cursor, DSH, Pi, OpenCode, and Antigravity.
- Windows can be configured after restore; WSL remains a separate later qualification.

## Clean qualification

- Empty-workspace restore: PASS, 439 verified/written files, zero conflicts.
- Official Windows artifact: PASS, SHA-256 matched, `herdr 0.9.0` verified.
- Inactive probe: READY / `QUALIFIED_INACTIVE`.
- Windows reviewer bridge: PASS, six configured reviewer roles.
- Codex-only supervised activation: PASS; eight other harnesses remained off.
- Provider-free tests: 63 passed, 2 skipped.
- Atomic rollback: PASS; all nine harnesses off and runtime inactive.
- No rebuild on toggle: PASS; binary digest, size, and mtime stayed unchanged.
- Provider/model calls: 0.

## Publication state

The Herdr kit update is local and uncommitted pending the required merge-update confirmation. No push has occurred for this update.
