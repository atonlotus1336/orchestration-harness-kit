# Additive parallel observation

**What it is:** an optional, provider-free feature profile for running at most
two independent read-only child tasks while observing an existing Orca worker.

**Why you would want it:** it can shorten independent inspection, comparison,
or review work without giving child agents write authority, provider authority,
or completion authority.

## Install this part

The module is included in the Kit payload but starts inactive. Copy the shipped
profile to a recipient-owned data directory. Keep the copy inactive until an
isolated harness profile has passed its local qualification and has an explicit
approval and rollback reference.

Do not copy an active profile, a receipt, or a skill projection from another
machine. Those are local operating evidence, not portable defaults.

## Test it

```text
python verify-additive-parallel.py
python ../conformance-kit/verify-additive-parallel.py
```

Both commands are provider-free. They do not launch children, call Orca, change
a model, or activate a harness.

## Safety boundary

- exactly two independent child packets are required for the feature path;
- every child is read-only and receives frozen, bounded context;
- ambiguity, shared mutable state, failed admission, and unsupported controls
  return serial fallback;
- only the parent may synthesize and verify a result;
- Orca may bind or observe an existing job, including `worker-show`; it cannot
  launch, command, approve, review, complete, or poll;
- a recipient-local rollback returns the feature to inactive while retaining
  evidence and admitting no new parallel batch.
