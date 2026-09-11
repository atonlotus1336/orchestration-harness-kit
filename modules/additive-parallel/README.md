# Additive parallelism

### Simple switches

`adaptive_switch.py` is the portable transaction coordinator;
`adaptive-switch.py` is an operational global/per-harness CLI once a recipient
supplies its local adapter module. Without both `--state-root` and
`--adapter-module`, it returns `ADAPTER_REQUIRED` with a nonzero exit code and
makes zero changes. Fresh installs stay inactive, and no keys, routes,
usernames, receipts, or process IDs are exported.

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
- Orca may bind or observe an existing job, including `worker-show`; it cannot
  launch, command, approve, review, complete, or poll.
- Transaction rollback covers recipient runtime adapters and exact paper-state
  bytes; an unverified restoration is never labeled successful.
- Installer rollback restores managed module bytes, keeps evidence, and admits
  no new work by itself.
