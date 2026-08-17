# Private Hermes System Snapshot

This repository carries a credential-free implementation snapshot of the current
Hermes ecosystem. Unlike the small portable contracts under `modules/`, this
snapshot preserves the actual source code, policies, tests, runbooks, and operating
scripts used by the owner's system.

## Included implementation

- Prime Agent bridge, supervisor, job/capability/learning ledgers, MCP service,
  governed session routing, recovery, rollout, and auto-handoff code.
- Oh My Pi runtime, worktree, policy, adapter, constrained-test, container, SBOM,
  feasibility, and live-gate implementation.
- Hermes profile synchronization, startup authentication, gateway repair, health,
  shared-skill, orchestration, RAG, and Ollama integration scripts.
- Hermes Telegram profile configuration, model validation, profile synchronization,
  test-message tooling, and Telegram command documentation.
- Auto-learning and skill-evolution implementation.
- Daily/weekly/monthly autoexperiment control plane, backup chain, promotion,
  ablation, judging, scheduling, tests, and operational backup/restore scripts.
- Autoexperiment research policy, contracts, adapters, templates, authority
  constitution, and system surface graph required by the control plane.
- Verified maintenance policy, runner, ledger, scheduler, runbook, and tests.

The exact file list and SHA-256 hashes are in
`system-snapshot/SNAPSHOT-MANIFEST.json`.

## Deliberately excluded

- `.env` files and backups
- API tokens, credentials, private keys, and Telegram bot secrets
- runtime ledgers, logs, caches, generated reports, and mutable state
- signed/live evidence receipts tied to a particular machine
- three adversarial tests containing fake token-shaped strings
- personal project surfaces and project-specific maintenance jobs

These exclusions are intentional. Restore credentials after installation through
the documented environment variables or local secret files.

Identity, host, and machine-topology strings are represented as named recipient
placeholders or `__PRIVATE_TOKEN_NN__`; self-hosted service URLs use example
domains. This includes usernames, owner handles, Tailscale addresses, and local
tool-storage locations.
Copy `system-snapshot/PLACEHOLDERS.template.json` to a file outside Git, replace
every `<local-value>`, and pass that file to the restore command.
`__WINDOWS_WORKSPACE__` and `__WSL_WORKSPACE__` are filled automatically from
`--workspace-root`.

Prime lifecycle tests that consume live attestation context or the pinned Prime
source checkout are environment-bound. After restoration, run the included Prime
acquisition and lifecycle scripts in WSL to recreate `.git/phase01-gap-lifecycle`
and fresh machine evidence. Portable supervisor, session-control, maintenance, and
ledger tests run directly from the snapshot.

## Verify or restore

Dry-run first:

```powershell
python tools/restore-private-system-snapshot.py --workspace-root D:\AgentWorkspace
```

Exercise a complete restore into an automatically removed temporary workspace:

```powershell
python tools/restore-private-system-snapshot.py --selftest
```

Restore into a new workspace:

```powershell
python tools/restore-private-system-snapshot.py `
  --workspace-root D:\AgentWorkspace `
  --substitutions D:\Private\hermes-restore-values.json `
  --apply
```

Updating an existing installation requires the explicit `--overwrite` flag. Review
the dry-run and take a backup before using it.

## Refresh from the live system

```powershell
python tools/sync-private-system-snapshot.py `
  --workspace-root D:\AgentWorkspace `
  --hermes-root D:\AgentWorkspace\hermes-agent-self-evolution `
  --autoexperiment-root D:\AgentWorkspace\scripts\autoexperiment `
  --ops-root D:\AgentWorkspace\scripts\ops
```

The exporter fails closed when it finds token-shaped credentials and regenerates
the hash manifest on success.
