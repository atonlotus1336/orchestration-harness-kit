# Phase 82 — Optional Activation Boundary

## Goal

Close the portable SDK goal without conflating source qualification with a live
deployment. Produce an explicit, profile-scoped promotion contract and stop at
the authorization boundary.

## Completion rule

The portable phase is complete when:

- every adapter remains `installed-not-activated` by default
- production activation remains disabled in the shared base policy
- the shadow → review → production sequence names quality, calls, latency,
  rollback, and token gates
- no provider call, service restart, live configuration edit, or activation is
  performed without a new profile-specific authorization

Actual deployment is optional operations work, not another SDK development
phase. There is no Phase 83.
