# Adaptive parallelism

Adaptive execution and reviewer concurrency are additive to the existing Code
Router, Code Evaluator, durable child sessions, and v1 read-only shadow
activation. The v1 shadow_active state still means at most two independent,
read-only native children; it is not migrated into coding authority.

The v2 strategy defaults to adaptive but ships inactive. Its closed rollout
lifecycle is inactive -> shadow -> canary -> pilot_limited -> pilot_wider ->
production, with review as the separately qualified reviewer-canary state.
Canary and limited pilots use a stable 10% lineage bucket; the wider pilot uses
50%. Production requires wider-soak, source-bound, unexpired grants for every
enabled layer. Execution, reviewer, career, temperament, and career-Shadow
grants are independently revocable and never translate a T lane into an S
pipeline or the reverse.

The Code Router and Code Evaluator both consume host-owned admission facts.
Missing capacity or a missing, forged, stale, expired, wrong-model, wrong-profile,
wrong-workload, or wrong-S-pipeline qualification falls back to the established
serial path. S0-S4 membership and model-call budgets remain owned by the
existing evaluator.

Use scripts/adaptive-parallel.py plan to inspect a transition without writing.
Qualification records are bound to harness, profile, model route, policy,
scheduler sources, workload class, capability, budgets, expiry, multilineage
capacity, and the S pipeline and career catalog when reviewing. Limited
production requires at least 24 hours and 20 lineages; wider production requires
72 hours and 50 lineages. Both time and volume must pass.

Rollback immediately stops new adaptive admissions, preserves evidence, leaves
existing children for governed cancellation/reconciliation, and records unknown
consumption as liability. It does not refund spent or unknown usage.

## Local activation state

Keep `config/adaptive-parallel-policy.json` inactive in Git. An installed host
can store its active policy at `.state/adaptive-parallel/policy.json`, with its
unchanged signed qualification store at `qualification.json` and history under
`receipts/` in the same directory. These machine-specific files are ignored.
The operator CLI, Prime coding service and reviewer use this local policy when
present; a fresh checkout continues to use the inactive shipped default.
Missing or invalid local qualification never falls back to historical grants.

Explicit API/CLI paths take precedence over environment paths, followed by
local state and then legacy defaults. Environment overrides are
`LAPART_ADAPTIVE_POLICY_PATH`, `LAPART_ADAPTIVE_QUALIFICATION_PATH`, and
`LAPART_ADAPTIVE_RECEIPTS_PATH`; use all three for a separate state directory.
After migrating a running installation, restart its consuming services to
reload the policy path. Moving state does not renew, widen or re-sign grants.
Keep signing keys in the existing private runtime environment, never in Git.

A policy state of `canary` is not proof that every layer can dispatch. Review
grants also require the trusted host's `LAPART_REVIEW_PROFILE`,
`LAPART_REVIEW_MODEL_ROUTE_SHA256`, and `LAPART_REVIEW_WORKLOAD_CLASS` to match
the signed record. Missing identity keeps reviews serial and career overlays
neutral. Do not accept these values from caller input or from the grant.
Provider-free verification does not replace live qualification or production soak.
