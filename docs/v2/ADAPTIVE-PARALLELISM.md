# Adaptive parallelism v2

Adaptive v2 is an optional, inactive module that computes bounded execution and
reviewer-admission facts. It also assigns qualified career methods to an already
selected reviewer panel. It does not launch work, choose the S0-S4 review depth,
change permissions, call a provider, or issue a completion verdict.

## Compatibility

The v1 profile and validator remain unchanged. V1 still means exactly two
independent read-only children, serial fallback on uncertainty, parent-only
synthesis, and observation-only Orca access. Installing v2 adds new files beside
v1 and does not migrate a recipient's local activation state.

V2 begins with `strategy=adaptive`, `activation_state=inactive`, disabled
execution and reviewer layers, and five separate disabled grants: execution
parallelism, reviewer concurrency, career kernel, temperament, and career-Shadow
counteranalysis. Career temperament labels are symbolic prompt metadata rather
than claims about people.

## Scheduler contract

`serial` admits at most one task and `fixed_two` admits at most two. `adaptive`
starts at the configured initial ceiling and advances one 1/2/4/8 tier per
complete verified streak. Shadow state computes desired capacity but always
admits zero. Canary admission also requires explicit candidate-canary authority;
canary and limited pilots select a stable ten-percent lineage bucket, while the
wider pilot selects fifty percent. Review-only state never admits execution.

Each work packet declares an absolute canonical `workspace_root`, relative
read/write/artifact paths, and either a complete nonempty `resource_set` or an
explicitly unknown resource state. Unknown resources conflict. So do shared
resources, dependencies, nested workspace roots, case aliases, directory
ancestor overlaps, and paths that resolve to the same symlink target. A path
that resolves outside the workspace fails validation.

The policy contains exactly five independently typed grants. Execution and
reviewer grants must match their layer enable flags. The career kernel depends
on reviewer concurrency; temperament and career-Shadow depend on the career
kernel. Every enabled grant requires a non-null expiry. Admission also checks the exact layer grant state, unexpired timestamp,
and a digest qualification reference. Missing or mismatched authority admits
zero.

## Install just this module

From `modules/additive-parallel`, preview the staged file set:

```text
python manage-adaptive-v2.py stage --target <recipient-module> --backup-root <recipient-data>/adaptive-backups
```

Review both paths, then confirm the write:

```text
python manage-adaptive-v2.py stage --target <recipient-module> --backup-root <recipient-data>/adaptive-backups --apply --yes
```

The result contains a content-derived snapshot ID. Existing module bytes are
copied to the recipient-owned backup root before the inactive templates are
staged. Recipient configuration, memory, credentials, activation records, and
runtime receipts are outside the managed file set.

## Verify

Run both generations and the additive conformance suite:

```text
python verify-additive-parallel.py
python verify-adaptive-v2.py
python ../conformance-kit/verify-additive-parallel.py
```

Maintainers with the canonical source repositories can add the parity check:

```text
python verify-adaptive-v2.py --source-workspace <source-workspace>
```

The check binds canonical source hashes, compares the packaged policy and career
catalog, and replays execution, reviewer, and career decisions. A source pass is
`SOURCE_TESTED`; it is not live qualification or activation.

## Wire a recipient adapter

Copy `adaptive-v2.adapter.example.json` into recipient-owned configuration and
fill only generic execute, review, cancel, and status ports. Keep every
capability false until the recipient has qualification evidence for the exact
harness, profile, model route, policy/catalog hashes, workload class, layer,
ceiling, expiry, and rollback target.

The adapter receives the recipient's frozen S pipeline, selected reviewer IDs,
call budget, and capacity facts. It may return staged batches, grant hashes, and
career assignments. The recipient's selector keeps membership authority and the
recipient's review controller keeps convergence and completion authority.

Candidate grants permit only predeclared canary trials. Temperament and
career-Shadow grants require the career kernel. Missing, disabled, expired, or
incompatible career evidence produces a neutral assignment. Missing reviewer
concurrency evidence keeps required reviewers serial where the baseline is
authorized; otherwise the recipient returns `INCONCLUSIVE`.

## Preview activation

Activation is a recipient operation, not a module-manager command. Before any
local change, record the current local activation bytes and verify these facts:

- the shipped policy remains inactive;
- the target capability and ceiling have an unexpired recipient qualification;
- parent/convergence and required-reviewer capacity is reserved first;
- total provider-call, token, time, and retry bounds are declared;
- rollback blocks new affected admission and retains usage liabilities;
- v1 state is not reinterpreted or overwritten.

Then produce a local preview showing desired and admitted execution/reviewer
counts, limiting reasons, selected lenses, career IDs, qualification identities,
and the exact rollback target. The portable module supplies no command that can
turn that preview into production state.

## Roll back staged module bytes

Preview restoration using the recorded snapshot ID:

```text
python manage-adaptive-v2.py rollback --target <recipient-module> --backup-root <recipient-data>/adaptive-backups --snapshot <snapshot-id>
```

After verifying the target and backup root, restore the exact prior bytes:

```text
python manage-adaptive-v2.py rollback --target <recipient-module> --backup-root <recipient-data>/adaptive-backups --snapshot <snapshot-id> --apply --yes
```

This restores module files only. It does not delete recipient evidence or treat
unknown usage as refunded. Runtime rollback and cancellation remain owned by the
recipient's harness controls.
