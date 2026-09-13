# Herdr evaluator provider

## Status

The Windows integration has a permanent, preconfigured reviewer bridge and a
governed capability starter. It is currently supervised for Codex only:

- tracked config/herdr-evaluator-adapters.json remains portable with mode off and
  no host-specific reviewer command.
- tracked config/herdr-runtime-lock.json remains inactive and runnable false.
- the ignored .state/herdr-review runtime overlay has mode supervised, runnable
  true, and six persistent reviewer commands pinned to the direct Codex Desktop
  executable and ChatGPT subscription home.
- the Prime capability service uses ConfiguredHerdrReviewStarter and validates the
  active lock, bridge hash, reviewer command, receipt, attempt, reviewer, and packet
  binding before authorizing a start.
- JSON remains the default reviewer transport.
- the Codex harness gate is supervised; every other harness gate is off.
- the permanent bridge passed its end-to-end S1 canary and a later bounded
  supervised soak: two S1 reviews plus a parallel two-reviewer S2 panel. The soak
  made four model requests with exact usage, no tool events, no retry, no source
  change, and no remaining evaluator session.

The pinned Windows runtime is Herdr 0.9.0 from source commit
9ad65d9031e8cb16a7b553c0e6f74809e9811e92. Its canonical API-schema digest is
7ae4593be31915e2cd851c61fe59687556a2ebaaad4142fba5a9edbc9bafe7d9.
The LapArt launcher is __WINDOWS_WORKSPACE__/herdr/herdr.cmd. WSL has a separately
checksum-verified Herdr 0.9.0 binary and Codex 0.153.1 reviewer command, but its
overlay remains off, inactive, and unrunnable. Schema output is decoded
explicitly as UTF-8 before canonical hashing so Windows and WSL share one pin.
The evaluator transport itself is pinned to the packaged direct Windows binary
at __WINDOWS_WORKSPACE__/herdr/.lapart-herdr/herdr.exe so cmd.exe wrapper behavior cannot
discard structured protocol acknowledgements.

## Boundary and data flow

    harness submission
      -> harness-neutral ingress
      -> existing S0-S4 evaluator policy and reviewer selection
      -> JSON transport (default) OR explicitly selected Herdr transport
      -> isolated temporary review packet
      -> fixed reviewer launcher
      -> closed JSON result file
      -> existing finding validation, reconciliation, receipt, and verdict owner

Herdr is a process host for selected S1-S4 reviewers. It does not classify risk,
choose the S-level, select reviewers, reconcile findings, apply patches, or issue
the final verdict. S0 remains deterministic and cannot use Herdr. Full mode does
not grant verdict authority.

Herdr is not a sandbox. The safety boundary is defense in depth: reviewer working
directories are temporary and outside the repository; source is copied as bounded
content rather than mounted; the environment is allowlisted; pane history and
scrollback retention are disabled; the launcher uses fixed argv without a shell;
and results are accepted only from a strict JSON file contract. The transport
exposes no tool names, arbitrary command field, patch channel, or source mount.

## Transport and trust rules

- Request and result envelopes use closed schemas and immutable receipt, attempt,
  reviewer, packet, and source lineage.
- Terminal output and pane state are operational signals, never review findings.
- Prompt, completion, and total usage must be exact and arithmetically consistent.
  Unknown usage remains unsettled and cannot be reused.
- A timeout or ambiguous mutation acknowledgement is never retried automatically.
  The isolate is quarantined for explicit reconciliation.
- Successful isolates are removed immediately. Ambiguous isolates retain only the
  bounded request/result/control artifacts for the configured retention period.
- Patch-like fields, commands, tools, mounts, replacements, malformed output,
  source drift, and lineage drift fail closed.
- S4 still requires its existing explicit confirmation and receipt binding.

## Runtime readiness

| Target | Schema state | Process readiness | Shipped behavior |
| --- | --- | --- | --- |
| Windows | Pinned and read-only probed | Supervised (runnable true) | Codex-only starts |
| WSL | Pinned and read-only probed | Configured, inactive, runnable false | No harness starts |

READY from the probe means the observed binary/schema matches the lock. Runnable
state still requires the adapter mode, Codex harness gate, governed capability
approval, and exact receipt binding. Process state such as started, waiting, done,
or ambiguous is likewise not a review verdict.

## Operator commands

Run these from __WINDOWS_WORKSPACE__/hermes-agent-self-evolution.

    python scripts/manage-herdr-review.py status
    python scripts/manage-herdr-review.py probe --executable __WINDOWS_WORKSPACE__/herdr/herdr.cmd
    python scripts/orchestration-control.py --json status --feature herdr-review --global

The first command reports adapter mode, lock readiness, schema pinning, and
configured reviewer count. The probe executes only --version and api schema
--json; it creates no workspace, pane, agent, or provider request.

The lifecycle is intentionally one control surface:

    python scripts/manage-herdr-review.py configure-windows --model gpt-5.3-codex-spark --reasoning low --confirm configure-herdr-review:windows:codex
    # Run inside __WSL_DISTRO__; configuration only, no activation:
    .venv/bin/python scripts/manage-herdr-review.py configure-wsl --herdr-executable __WSL_WORKSPACE__/herdr/.local/wsl/0.9.0/herdr-linux-x86_64 --codex-executable __WSL_WORKSPACE__/herdr/.local/wsl/codex/0.153.1/codex-x86_64-unknown-linux-musl --codex-home /home/__LINUX_USER__/.codex --model gpt-5.3-codex-spark --reasoning low --confirm configure-herdr-review:wsl:codex
    python scripts/manage-herdr-review.py activate --harness codex --model gpt-5.3-codex-spark --reasoning low --confirm activate-herdr-review:supervised:codex
    python scripts/manage-herdr-review.py deactivate --confirm deactivate-herdr-review
    python scripts/manage-herdr-review.py rollback --snapshot-id SNAPSHOT --confirm rollback-herdr-review

configure-windows and configure-wsl are installation/configuration operations.
Windows configuration must run while Herdr is off; WSL configuration writes a
separate inactive overlay and does not change any harness gate. Normal activate
and deactivate operations do not reinstall,
regenerate reviewer commands, or rebuild Herdr. They atomically update the
adapter, runtime lock, and every harness gate under one operation lock, emit
snapshots and receipts, verify the final state, and roll activation back on any
failure. Deactivation is fail-closed and also stops/deletes evaluator sessions.
The legacy set-mode path now permits only off and delegates to this unified
deactivation operation.

Tracked configuration is always the portable, fail-closed default. Machine-local
reviewer commands, executable hashes, provider profile, and activation state live
only under .state/herdr-review/, which is ignored by Git. Missing local state
therefore leaves a fresh checkout off; it cannot inherit this machine's activation.

Disabling does not reinterpret an ambiguous result; quarantined attempts require
explicit reconciliation and retain unknown usage. The low-level orchestration
switch remains defense in depth, but it is not the routine operator toggle.

## Activation and rollback

Installation/configuration and activation are separate. The permanent Windows
bridge, exact direct Codex executable, reviewer profile, and command hashes are
configured once. Activation then requires those exact values and refuses drift;
it only turns on supervised mode for Codex. Full mode is intentionally unavailable.
A live subscription-backed canary requires separate authorization because it
makes a model/provider call.

Rollback restores the selected atomic snapshot of adapter, runtime lock, and gate
state. It does not uninstall Herdr or delete the persistent bridge. This makes
activation/deactivation fast and repeatable without migration or rebuilding.

## Qualification limits

Provider-free qualification covers schema pinning, Windows and WSL readiness
models, S0-S4 boundaries, parallel isolated fixtures, event reordering,
no-redelivery behavior, exact accounting, source immutability, malicious output,
switch controls, JSON fallback, and all supported harness adapters. It does not
claim reviewer quality, provider compatibility, cost, latency, or live Herdr
session reliability beyond the bounded canary. The permanent bridge has now
passed one replacement end-to-end S1 canary and a bounded permanent-bridge
S1/S2 soak. Broader latency and reviewer-quality claims remain outside this
qualification. The later activation state and soak supersede the historical
runtime-state fields in evidence/herdr-evaluator/qualification.json; see
evidence/herdr-evaluator/supervised-soak.json.
