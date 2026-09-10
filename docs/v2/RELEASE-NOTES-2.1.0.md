# Release notes 2.1.0

## Added

- An optional additive-parallel observation module with a max-two independent
  read-only child profile, deterministic serial fallback, parent-only synthesis,
  and provider-free verification.
- A separate feature conformance suite so the closed one-case-per-operation
  protocol conformance suite remains unchanged.
- An Orca observation-only boundary that permits binding and status observation
  of an existing host job but denies launch, command, approval, review,
  completion, and polling authority.

## Compatibility and safety

- Protocol generations 1 and 2, the core activation enum, and adapter
  capability manifests are unchanged.
- The shipped profile is inactive. Installing the module starts no children,
  calls no provider, changes no model or service, and does not activate a
  harness.
- A recipient-local feature overlay may map to core `shadow` only after its own
  qualification, explicit approval, and rollback rehearsal.
