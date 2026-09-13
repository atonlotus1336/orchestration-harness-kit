# Governed Orca execution provider

The Orca integration is an optional execution transport for every harness in
the shared coding registry: Codex, Hermes, Claude Code, MOA, Cursor, DSH, Pi,
OpenCode, and Antigravity. It adds Orca runs, task dependency graphs,
supervised workers, persistent messaging, decision gates, retries, remote
placement, and release/retain lifecycle controls without replacing the Code
Router, Prime, OMP, the JobLedger, memory, or the evaluator.

## Authority boundary

- The deterministic Code Router still selects T0–T4 and its `actual_lane` is
  authoritative.
- `orca.worker.start` is an action with `approval: required` in the existing
  capability broker. A switch set to `full` permits the request; it does not
  approve it.
- The host creates and owns the isolated worktree and binds it to the existing
  Prime job identity before Orca launches a worker.
- Orca `worker_done`, task updates, and messages are evidence only. They never
  complete the Prime job, import a patch, merge, publish, promote memory, or
  issue an S0–S4 verdict.
- S0–S4 review remains on the existing evaluator transport so reviewer-set,
  budget, evidence, and verdict authority cannot drift.
- Existing MemoryWeaver and shared auto-memory remain canonical. Orca messages
  are not silently promoted into durable memory.

## Modes

The unified switch has one Orca row per harness:

- `off`: no active integrated worker and no new Orca operation. Entering this
  state requires a verified drain.
- `observe`: observation only; the default for a new or migrated installation.
- `quiesce`: deny new work while permitting inspection, stop, release, and
  other cleanup needed to drain existing workers.
- `supervised`: local worker lifecycle, messages, reads, and retry; no remote
  placement, task-DAG mutation, or gates.
- `full`: all supported Orca lifecycle operations, still bounded by approval,
  worktree scope, budgets, evidence, and evaluator controls.

Use the human-facing front door. It discovers the canonical switch state,
Prime ledger, provider catalog, and workspace automatically:

```text
python scripts/orca-control.py status
python scripts/orca-control.py on
python scripts/orca-control.py off
python scripts/orca-control.py doctor
python scripts/orca-control.py on --harness hermes
```

The default target set is Codex, Hermes, MOA, Cursor, DSH, Pi, and OpenCode.
Claude Code and Antigravity remain registered but are deferred; inspect them
explicitly with `--all-harnesses` without counting them in priority rollout
readiness. `status` and `doctor` report real runtime and production
qualification as `ACTIVE`, `PARTIAL`, or `BLOCKED`, followed by the exact next
action for each harness. `--json` is available for automation. Advanced path
overrides remain available but are not required for the standard installation.

`orca-control off` always uses the lifecycle controller: it quiesces new work,
drains existing workers, verifies zero remain, and only then records `off`.
The lower-level `orchestration-control.py` remains available for compatibility
and other orchestration features, but it is not the normal Orca interface.

The legacy Orca observer command remains as a compatibility facade. It now
reads and writes the same unified state and does not create a second switch:

```text
python scripts/orca-observer.py --json on --global
python scripts/orca-observer.py --json off --harness codex
```

## Drain, movement, and removal

The provider is registered lazily through
`config/parallel-worker-providers.json` and implements the generic
`ParallelWorkerProvider` contract. Prime does not import Orca at module load.
The catalog binds a provider to `router`, `evaluator`, or both consumers, so a
move changes configuration rather than implementation files:

```text
python scripts/manage-parallel-provider.py bind --consumer evaluator
python scripts/manage-parallel-provider.py bind --consumer router --consumer evaluator --apply
```

`orca off` first writes `quiesce`, drains every integrated worker found in the
canonical Prime ledger, verifies zero remain, and only then writes `off`. An
incomplete or unverifiable drain stays visibly quiesced and exits nonzero.
Supply `--ledger` and `--allowed-root` when the canonical ledger cannot be
discovered from `LAPART_PRIME_LEDGER` or the standard Hermes state path.
Failures before a worker dispatch is requested close the exact worker and
coordinator terminals and settle as failed, so they cannot become permanent
paper-active records. An ambiguous failure after a dispatch request remains
`outcome_unknown` and must be reconciled rather than guessed.

Provider removal is transactional and preserves ledger evidence:

```text
python scripts/manage-parallel-provider.py unregister --ledger <jobs.db>
python scripts/manage-parallel-provider.py unregister --ledger <jobs.db> --apply
python scripts/manage-parallel-provider.py uninstall --ledger <jobs.db> --apply
```

Unregister removes the provider catalog entry and broker alias. Uninstall also
removes provider-owned files after backing them up. Neither operation deletes
Prime jobs, worker bindings, events, Orca execution rows, or switch receipts.
The returned snapshot can restore the prior registration and files.
## Adapter registry and qualification

`config/orca-harness-adapters.json` declares either an Orca-native agent or a
terminal command shim for all nine harnesses. `configured` means only that the
descriptor is present. `canary` and `production` must be earned with a live
runtime probe and a provider-free or subscription-backed lifecycle canary for
that exact harness. Missing commands fail closed and do not cause fallback to a
different model or provider.

Terminal shims use their bounded screen-readiness contract. Agent-specific
Orca `tui-idle` detection remains required for Codex, but is not imposed on
Hermes or another configured terminal shim whose TUI grammar Orca does not
natively recognize.

Hermes terminal-direct execution remains an Orca-owned lifecycle: Orca creates
the isolated worktree, coordinator, run, task, and worker terminal, while the
parent accepts only a stable proposal inside the receipt's declared paths and
performs collection plus tests itself. WSL launches suppress Python bytecode
artifacts so generated caches cannot masquerade as source-scope violations.
The management CLI uses governed Orca discovery unless `--orca` explicitly
overrides the executable.

The operator CLI reports configuration separately from runtime readiness:

```text
python scripts/manage-orca.py status --ledger <jobs.db> --allowed-root <root>
python scripts/manage-orca.py probe --harness codex --ledger <jobs.db> --allowed-root <root>
```

Mutating calls consume a closed JSON object on stdin. Starting a worker also
requires the broker-issued capability context; constructing that object outside
the broker does not create authority because the matching authorization event
must exist in the JobLedger.

## Supported Orca lifecycle

The provider supports worker start/show/read/check/send/reply/inbox/list,
stop/abandon/release/retain, explicit retry with compare-and-swap dispatch
rebinding, task create/list/update with dependencies and parents, decision gate
create/list/resolve, and exact saved-environment placement. Every operation is
switch-gated and auditable. Output and message bodies are represented in the
Prime ledger by hashes and bounded metadata.

## Adaptive production dispatch

The Code Router may select Orca automatically only for the authoritative
`actual_lane` values `T3_PRIME_OMP` and `T4_AUTONOMOUS`, and only when at least
two independent scopes were admitted. Selection also requires the provider to
be bound to the router, the harness switch to be `full`, a live READY probe, a
production adapter, and an authority-signed qualification bound to the exact
harness, route fingerprint, adapter launch type, Orca executable, qualification
contract, and admitted ceiling. Any missing, materially changed, mismatched, or
unverifiable condition falls back to the existing native route before an Orca
side effect.

Automatic starts use a single-use preauthorization for the exact normalized
`orca.worker.start` arguments. The global capability remains approval-required;
this narrow authority exists only after the signed production selector has
accepted the batch. Workers run in path-admitted isolated worktrees. Their
outputs remain proposals: the parent applies them serially, runs aggregate
verification, and rolls back the batch if integration fails.

A native Orca/Codex start whose result is temporarily unobserved is reconciled
against exactly one durable run/task dispatch. If Codex is ready with the task
still present as a draft, the provider submits that same draft once. It never
stops and duplicates an ambiguous dispatch.

Qualification can be run with a subscription-backed Luna route:

```text
python scripts/qualify-orca-coding.py --harness codex --model gpt-5.6-luna --adapter-launch native --promote-registry
python scripts/qualify-orca-coding.py --harness hermes --profile workspace --promote-registry
```

The qualifier selects the configured adapter for the named harness, compares
two real native coding canaries with the same two-worktree Orca workload, and
adds that harness to the signed store without discarding valid records for
other harnesses. Hermes uses its configured workspace profile and route; it
does not silently substitute a provider or model. Harnesses without a real
native baseline driver fail closed and remain `configured` until qualified on
a recipient that can run them.

Qualification records do not expire with elapsed time. They remain valid until
a material fingerprint changes: the harness route, adapter, Orca executable,
qualification contract, or admitted parallel ceiling. Runtime readiness is
still probed, and any mismatch falls back to the native route. Upgrade an older
signed v1.0 record without model calls using:

```text
python scripts/qualify-orca-coding.py --state-root <state-root> --migrate-persistent
```

Migration accepts only an authority-valid legacy record that has not already
expired, removes `expires_at`, records `validity=until_material_change`, and
re-signs the multi-harness store locally.

The generated authority material and A/B evidence stay in recipient-local
`.state`; they are never part of the portable kit.
## Lower-model operation

The control plane is deterministic Python and JSON. A lower-capability model
such as ChatGPT Luna receives closed operation names, bounded arguments, an
explicit harness adapter, and a preselected worktree. The model does not need
to infer routing, reviewer membership, authority, or completion state.
