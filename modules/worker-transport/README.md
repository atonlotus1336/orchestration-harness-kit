# Portable worker metadata

This module attaches worktree identity and external status observations to an
existing job ledger. It does not launch agents, approve execution, complete jobs,
select review tiers, merge changes, or delete worktrees.

Use it when several harnesses need the same reconnect and duplicate-event rules.
The host keeps its current scheduler, evidence completion and permission checks.
No model call is needed to collect Git identities or record observations.

## Install this part

Use Python 3.11 or later, Git, and `jsonschema`. Copy this directory. Supply the
existing owner-created `JobLedger` from the kit's system snapshot, or a qualified
compatible port. Do not create a second ledger to make this module start.
The bundled Git snapshot helper has no evaluator or execution-target imports.

Add this directory to the host's module search path, then use:

```python
from worker_port import WorkerTransport

worker = WorkerTransport(
    existing_ledger,
    target={"target_id": approved_target_id, "policy_sha256": approved_policy_sha256},
    external_source=approved_status_source,
)
```

Only trusted host configuration supplies those values. The policy fingerprint
must cover the target, external source, transport and allowed roots. A change to
any binding requires a new fingerprint and host reconciliation. An event cannot
enroll its own target, choose the ledger, or authorize a command.

The ledger port supplies `path`, `allowed_roots`, `get`, `_connect`, `_event` and
`_name` with the existing owner semantics. The same database receives two worker
extension tables; canonical jobs and their transitions stay with the owner.

Call `prepare`, `reconnect`, `reconcile`, `collect`, or `preserve` through the host.
Call `bind_external` from the host before accepting `observe_external` events.
The closed event schema rejects commands, prompts, approvals and review verdicts.
External `done` is an observation, not evidence of verified completion.

## Transport boundary

There is no default host, username, key, repository, shell, or SSH command here.
Use the existing topology and authenticated transport owner to deliver bounded
JSON to a fixed host endpoint. Keep strict host-key and capability checks there.
This module does not install that endpoint or qualify an SSH/CLI integration.
Do not expose these Python methods as an unauthenticated network API.

## Test it worked

From this directory, run:

```bash
python verify.py
```

The self-test uses the shipped ledger and real temporary Git worktrees. It
checks changed bytes, preservation, duplicate events, forged bindings, changed
targets, dirty source and escaping paths. Fixtures do not prove live SSH or
upstream CLI behavior. Qualify each real harness and transport separately before
activation. Tests do not change an installed host's policy or invoke models.

`PROVENANCE.json` identifies the curated source sections and generated text.
File fingerprints normalize a UTF-8 BOM, line endings and final blank lines so
the sanitizer's text encoding does not invalidate unchanged code. All remaining
content is hashed. Regenerate from the canonical source when those sections change; do not
silently fork their security behavior. Generation excludes host-specific SSH
helpers and substitutes recipient-owned target and event-source bindings.
