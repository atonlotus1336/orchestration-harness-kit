# Additive parallel observation profile

## Purpose and scope

Version 2.1 adds an optional profile for a narrow form of parallel work: two
independent, read-only child assignments. It is a feature profile, not a new
protocol operation, adapter capability, scheduler, provider route, or
completion authority.

The portable Kit always ships this profile inactive. A recipient may map a
locally qualified feature activation to the existing core `shadow` state, but
must keep that receipt in recipient-owned data. Installation alone starts no
children and changes no harness, model, provider, service, or skill.

## Plan and acceptance gates

1. Install the optional module and run both provider-free verifiers.
2. Copy the template to a recipient-owned data directory. Preserve the shipped
   file unchanged.
3. Bind one isolated harness profile and record the pre-change feature state.
4. Obtain local approval, a rollback reference, and a call/token cap of zero.
5. Prove a valid two-child read-only replay, serial fallback for ambiguity,
   parent-only synthesis, duplicate and wrong-identity event rejection, and
   Orca observation-only behavior.
6. Write a local receipt that maps feature `shadow` to core `shadow` and hashes
   the local approval reference.
7. Roll back to inactive, preserve evidence, and confirm no new child batch is
   admitted.

No step grants parallel writes, recursive delegation, provider/model changes,
new provider consumption, external side effects, or Orca command/approval/review
or completion authority.

## Orca boundary

The feature permits only `bind-orca`, `observe-orca`, and
`observe-orca-worker-show` against a host-bound existing job. A `done` report
records an observation. It cannot complete the canonical job or start review.

## Compatibility

The profile leaves protocol generations 1 and 2 untouched and does not alter
the primary conformance suite, whose one-case-per-operation invariant remains
closed. Feature conformance is a separate provider-free scenario runner.

## Rollback

Set the recipient-local feature state and mapped core state to `inactive`, stop
new feature admission, and retain the local evidence. Do not delete evidence or
automatically terminate existing children. Continue serially until a fresh local
local approval process approves the qualification.
