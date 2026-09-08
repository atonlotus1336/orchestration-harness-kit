# Provider lease and backend admission

The shared core can reject an incompatible provider/backend route before any
model call. This is a deterministic admission check, not another router or
review tier.

## Ownership boundary

- T0-T4 continues to select execution depth.
- S0-S4 continues to select review depth.
- Provider/backend admission selects a compatible implementation for an
  already-authorized execution lane.
- Existing approval, capability, activation, and state owners remain authoritative.

## Route facts

Each `ProviderRoute` carries two credential-free records:

- `ProviderLease`: authentication modes, internal or distributable use,
  subscription-delegation permission, retention classes, compliance classes,
  and current/stale/unverified terms status.
- `BackendContract`: supported task shapes, durable-resume support, measured
  framework overhead, tested version range, qualification status, and the
  events that require requalification.

Never put credentials, prompts, source, or command output in either record.
Recipient-specific facts belong in ignored local configuration and should be
validated against `modules/orchestration-core/provider-route.schema.json`.

## Automatic refusal conditions

`provider.resolve` returns `PROVIDER_CONSTRAINT_UNSATISFIED` when a requested
route cannot meet any declared requirement. This includes:

- distributable-product use through subscription authentication when delegation
  is not explicitly allowed;
- a stale or unqualified backend;
- a backend version outside its qualified range;
- missing durable resume for a workflow that requires it;
- unknown or excessive overhead when a latency budget is declared;
- incompatible authentication, retention, or compliance requirements;
- stale or unverified terms when current terms are required.

Absence of a requirement preserves backward-compatible selection. Unknown facts
never satisfy an explicitly requested constraint.

## Compatibility and requalification

The adapter compatibility matrix records either a numeric half-open range or a
source-contract mode. A backend version change, source-hash change, or terms
status change invalidates the prior qualification. Re-run provider-free
conformance and target-specific qualification before restoring `qualified`.

## Passive timing

Telemetry may record non-negative millisecond counts for adapter, routing,
provider wait, tool execution, handoff, evidence collection, and persistence.
It must not retain content. Native SDK tracing remains outside this contract and
must not become an evidence or lifecycle authority.

The local governed execution path automatically records durable steps for T4,
for decomposed work, and for T3 when the caller declares durability required.
T0/T1 remain unaffected.
