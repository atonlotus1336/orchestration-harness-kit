# Phase 79 — Portable Lifecycle and Rollback

## Goal

Replace manual per-harness copying with one provider-free controller for install,
reference-only connect, doctor, verify, upgrade, rollback, and managed uninstall.

## Coding route

The required governed evaluator classified this plan as actual lane T3 using
`--execution-value-only`. That evaluation made no provider call.

## Exact outputs

- lifecycle commands in `installer/kit.py`
- v1-compatible managed-file, connection, and rollback schemas
- a provider-free current-source lifecycle verifier and receipt
- lifecycle regression, rollback, tamper, privacy, and preservation tests
- end-user install, update, rollback, and uninstall documentation
- a truthful historical boundary around the immutable Phase 78 receipt

## Acceptance criteria

1. All mutating commands remain dry-run without both `--apply` and `--yes`.
2. Connect supports Hermes, OpenCode, Pi, Antigravity, Codex, Claude Code, and
   MOA through thin adapters and reference-only configuration.
3. Connect does not activate a harness or call a provider.
4. Install and upgrade preserve recipient-local configuration and connections.
5. Upgrade snapshots every managed file and automatically restores on failure.
6. Rollback verifies the complete snapshot before mutation and automatically
   recovers the current state if restoration fails.
7. Uninstall removes only manifest-owned files and preserves recipient data.
8. v1 managed manifests remain accepted.
9. The clean public payload reproduces all current-source verifiers.

## Threat model

- path traversal or a broad root could turn lifecycle operations destructive
- a literal secret could be persisted as a connection value
- a manifest-only snapshot identity could alias different v1 file contents
- a corrupt snapshot could partially overwrite the current install
- reinstall could orphan local connection files or replace local configuration
- mutable installer work could falsely invalidate immutable adapter evidence

## Rollback, cost, and activation impact

The commit can be reverted as one source change. Installed upgrade operations
also maintain content-addressed per-install snapshots under the selected data
root.

- Provider calls and tokens: 0
- Pay-per-token and DSH calls: 0
- Service restarts: 0
- Live configuration, model, provider, or activation changes: 0
- Tags, pushes, and default-branch merges: 0
