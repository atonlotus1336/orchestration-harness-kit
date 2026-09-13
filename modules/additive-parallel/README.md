# Additive parallelism

### Simple switches

`adaptive_switch.py` is the portable transaction coordinator;
`adaptive-switch.py` is an operational global/per-harness CLI once a recipient
supplies its local adapter module. Without both `--state-root` and
`--adapter-module`, it returns `ADAPTER_REQUIRED` with a nonzero exit code and
makes zero changes. Fresh installs stay inactive, and no keys, routes,
usernames, receipts, or process IDs are exported.

The legacy Orca observer grammar is now a compatibility facade over the same
unified `orchestration-switches.json`; it no longer owns a second state file:

```text
python orca-observer.py --state-root <recipient-data>/adaptive-switch on --global
python orca-observer.py --state-root <recipient-data>/adaptive-switch off --harness codex
python orca-observer.py --state-root <recipient-data>/adaptive-switch status
```

The portable facade starts inactive and can safely enter or leave `observe`.
Execution modes must use the recipient lifecycle adapter so `off` can quiesce,
drain, and verify zero active workers rather than changing paper state alone.

Adaptive v2 also exports the canonical governed Orca execution provider under
`prime_bridge/`, its operator CLI under `scripts/`, a complete nine-harness
adapter registry, and the authority contract in
`docs/ORCA-EXECUTION-PROVIDER.md`. The recipient must already provide the Prime
`JobLedger`, `WorkerTransport`, capability broker, and unified switch modules;
the provider deliberately will not invent those authorities. Installation and
staging remain inactive and perform no Orca or model call.

After staging into a compatible recipient, use one Orca front door. It finds
the standard switch state, Prime ledger, provider catalog, and workspace:

```text
python scripts/orca-control.py status
python scripts/orca-control.py on
python scripts/orca-control.py off
python scripts/orca-control.py doctor
python scripts/orca-control.py on --harness hermes
```

Commands target Codex, Hermes, MOA, Cursor, DSH, Pi, and OpenCode by default.
Claude Code and Antigravity remain registered but deferred; include them only
for inspection with `--all-harnesses`. `off` always uses recipient-owned
quiesce, drain, and zero-worker verification. The lower-level portable switch
below remains a provider-free contract, not the normal production Orca UI.

Small models and first-time operators must follow the literal build, setup,
readiness, qualification, drain, and verification sequence in
`docs/ORCA-EXECUTION-PROVIDER.md#small-model-build-and-setup-runbook`.
Never infer `ACTIVE` from installed files or a `full` switch alone.

The kit also exports an inactive-by-default unified switch contract for route,
review, learning, autonomy, memory, token-compute, and RAG:

```text
python orchestration-control.py --state-root <recipient-data>/orchestration status
python orchestration-control.py --state-root <recipient-data>/orchestration all full --global
python orchestration-control.py --state-root <recipient-data>/orchestration memory off --harness codex
```

Recipient adapters consume `orchestration_switch.mode_for()`. Full requests
remain bounded by recipient qualification, authorization, budgets, approvals,
evidence, and containment. Installation performs no activation or provider call.

**What it is:** an optional, provider-free package with two compatible tracks:
the unchanged v1 profile for exactly two independent read-only child tasks, and
an inactive adaptive v2 template for execution admission, staged reviewer
concurrency, and deterministic career-method assignment.

**Why you would want it:** a recipient can test safer scaling and broader review
coverage without giving this module permission to choose review depth, launch a
provider call, change permissions, or declare work complete.

## Install this part

Both generations start inactive. For v1, copy
`additive-parallel.profile.json`. For v2, preview a staged install:

```text
python manage-adaptive-v2.py stage --target <recipient-module> --backup-root <recipient-data>/adaptive-backups
```

Apply only after reviewing the paths:

```text
python manage-adaptive-v2.py stage --target <recipient-module> --backup-root <recipient-data>/adaptive-backups --apply --yes
```

The command backs up prior module bytes and reports a snapshot ID. It copies an
inactive policy and disabled capability-port template. It does not create or
modify a recipient activation record.

## Bind and operate the switches

The recipient adapter module exports a nonempty `HARNESS_REGISTRY` tuple and
`build_adapters(state_root)`. Each returned adapter must implement:

- `prepare(transaction_id, desired_state)` -> `{"state": "PREPARED"}`
- `commit(transaction_id)` -> `{"state": "COMMITTED"}`
- `rollback(transaction_id)` -> `{"state": "ROLLED_BACK"}`
- `finalize(transaction_id)` -> `{"state": "FINALIZED"}`
- `verify()` -> `{"state": "ACTIVE"}` or `{"state": "INACTIVE"}`

`refresh()` -> `{"state": "REFRESHED"}` is additionally required for the
`refresh` command. Adapter rollback must be idempotent and restore the
pre-transaction runtime state even if prepare or commit only partly completed.

```text
python adaptive-switch.py --state-root <recipient-data>/adaptive-switch --adapter-module <recipient-adapter.py> on --global
python adaptive-switch.py --state-root <recipient-data>/adaptive-switch --adapter-module <recipient-adapter.py> off --harness codex
python adaptive-switch.py --state-root <recipient-data>/adaptive-switch --adapter-module <recipient-adapter.py> status
```

Activation and deactivation use the same prepare/commit/verify/paper-state
transaction. A failed operation reports `FAILED_ROLLED_BACK` only when every
adapter and the exact prior paper-state bytes were independently verified as
restored. Otherwise it reports `ROLLBACK_INCOMPLETE`, exits nonzero, and leaves
the mismatch visible as `INVALID`. `FINALIZE_INCOMPLETE` means the requested
state is active and verified but adapter transaction cleanup needs attention.

### Adaptive Orca production integration

The kit now includes the deterministic T3/T4 provider selector, the
parent-owned adaptive batch transport, and the real coding A/B qualifier. They
stage as code only and remain inactive until the recipient supplies all of the
existing authority seams: Code Router `actual_lane`, adaptive admission,
`JobLedger`, `WorkerTransport`, capability policy/ledger, unified switch,
runtime probe, host signing authority, and a recipient-local qualification.

Production selection requires two or more admitted scopes and binds the signed
qualification to the exact harness, route fingerprint, adapter launch type,
parallel ceiling, Orca executable, adapter content, and qualification contract.
Automatic worker starts use exact-argument,
single-use preauthorization. The global `orca.worker.start` capability remains
approval-required outside that qualified path. Results remain isolated
proposals which the parent applies serially and verifies as a batch.

No `.state` directory, HMAC, route receipt, job ledger, user-specific executable
path, or production adapter status is shipped. The included adapter registry is
portable and `configured` only. Recipients qualify their own subscription-backed
runtime with `scripts/qualify-orca-coding.py`; a missing or materially stale
qualification falls back to the existing native route before an Orca side effect.
### Move or remove Orca

Orca implements the generic `ParallelWorkerProvider` contract and is loaded
lazily from `config/parallel-worker-providers.json`. The catalog binds it to
the `router`, `evaluator`, or both; moving it is a configuration transaction:

```text
python scripts/manage-parallel-provider.py bind --consumer evaluator
python scripts/manage-parallel-provider.py bind --consumer evaluator --apply
```

After verified `off`, `unregister` removes the catalog entry and broker alias.
`uninstall` additionally snapshots and removes only provider-owned files.
Both preserve Prime ledger evidence and support snapshot restore. The recipient
must supply its canonical ledger and existing Prime dependencies.
## Test it

```text
python verify-additive-parallel.py
python verify-adaptive-v2.py
python ../conformance-kit/verify-additive-parallel.py
```

All commands are provider-free. They do not launch children or reviewers, call
a model, change a provider, select an S pipeline, or activate a harness. Kit
maintainers can also compare current canonical sources and decisions:

```text
python verify-adaptive-v2.py --source-workspace <workspace-containing-source-repositories>
```

## Preview, migration, and rollback

V2 is additive: the v1 profile, validator, receipt schema, and exact max-two
read-only meaning remain separate. Staging v2 never converts an active v1 local
record. Wire the generic capability ports only in an isolated recipient profile,
then qualify each layer and ceiling under recipient-owned grants.

To restore the exact earlier module bytes, first preview and then confirm using
the snapshot ID returned by `stage`:

```text
python manage-adaptive-v2.py rollback --target <recipient-module> --backup-root <recipient-data>/adaptive-backups --snapshot <snapshot-id>
python manage-adaptive-v2.py rollback --target <recipient-module> --backup-root <recipient-data>/adaptive-backups --snapshot <snapshot-id> --apply --yes
```

To remove only the managed module files while preserving recipient-local files,
preview and confirm `uninstall`. It creates a rollback snapshot first:

```text
python manage-adaptive-v2.py uninstall --target <recipient-module> --backup-root <recipient-data>/adaptive-backups
python manage-adaptive-v2.py uninstall --target <recipient-module> --backup-root <recipient-data>/adaptive-backups --apply --yes
```

## Safety boundary

- V1 still requires exactly two independent read-only child packets.
- V2 installs with execution, reviewer concurrency, career, temperament, and
  career-Shadow grants disabled.
- V2 `serial` and `fixed_two` strategies cap admission at one and two;
  `adaptive` climbs the 1/2/4/8 ladder one tier per complete verified streak.
- Shadow computes observations but admits zero. Canary and pilot modes require
  the exact grant state and use stable lineage buckets; candidate canaries also
  require explicit authorization.
- Work packets use a canonical workspace root and complete-or-unknown resource
  facts. Case aliases, directory ancestry, symlink aliases, nested roots,
  shared resources, and dependencies all fail closed as conflicts.
- V2 admission ceilings are constrained by qualification, host capacity,
  parent/reviewer reserves, backlog, cooldown, and adverse feedback.
- The adapter receives an already selected S pipeline and reviewer set. It may
  stage them but may not change membership or issue the authoritative verdict.
- The twelve careers supply catalog-bound methods only. Zodiac temperament is
  symbolic prompt metadata, and the career-Shadow pass is typed counteranalysis
  within the same reviewer call.
- Ambiguity, shared mutable state, failed admission, and unsupported controls
  return serial fallback.
- Only the parent may synthesize and verify a result.
- The v1 Orca observer may only bind or observe an existing job, including
  `worker-show`.
- The optional v2 Orca execution provider may launch and coordinate only after
  the recipient capability broker authorizes an approval-required action and
  the host binds an isolated worktree. It cannot authorize itself, select the
  route or reviewer set, issue the evaluator verdict, or complete the canonical
  job.
- `quiesce` denies new Orca work while allowing inspection and cleanup. `off`
  requires a recipient-verified drain; an incomplete drain remains quiesced.
- Transaction rollback covers recipient runtime adapters and exact paper-state
  bytes; an unverified restoration is never labeled successful.
- Installer rollback restores managed module bytes, keeps evidence, and admits
  no new work by itself.
