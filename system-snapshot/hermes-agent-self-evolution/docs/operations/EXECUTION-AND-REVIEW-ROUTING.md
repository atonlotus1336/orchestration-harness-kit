# Execution and review routing

T0–T4 chooses the executor. S0–S4 chooses review depth. They are independent;
there is no T2=S2 mapping, and increasing one does not automatically increase the other.

## Execution lanes

| Lane | Eligible shape | Execution |
| --- | --- | --- |
| T0 | Exact single-file, low-risk task with an explicit assertion | Direct tools |
| T1 | Single-file, low-risk task with an explicit assertion | Native agent |
| T2 | Bounded low-risk task within the OMP-lite file limit | OMP proposal plus host verification; omits the Prime model-review turn |
| T3 | Coupled, complex, higher-risk, or insufficiently evidenced work | Prime plus OMP |
| T4 | Explicit operative autonomous intent or explicit unattended flag | Durable autonomous execution |

The current gated policy promotes T0/T1. T2 remains recommended-only and executes
as T3 until the existing matched quality/cost promotion gates pass. This update
does not promote T2 or change provider routes. Inspect `recommended_lane`,
`actual_lane`, `policy_override`, and `actual_lane_reason`. The original `reason`
explains the recommendation; `actual_lane_reason` explains the executable route.
Parallel-safety advice follows the actual lane.

Autonomy classification uses the shared operative-intent detector. Discussion,
quoted examples, and tested negative instructions do not activate T4. Invalid
risk tiers, empty assertions, and malformed input types are rejected.

## Review pipelines

| Pipeline | Purpose | Configured maximum reviewer calls |
| --- | --- | --- |
| S0 | Facts, deterministic checks, receipt | 0 |
| S1 | Localized review | 1 |
| S2 | Review plus evidence audit and triggered specialist | 3 |
| S3 | Structural/architecture review plus evidence audit | 5 |
| S4 | Explicit system audit, debt and improvement review | 7 |

These are ceilings, not call targets. The root adaptive-review policy remains in
shadow mode; evaluating a route does not launch reviewers. S4 retains its existing
confirmation boundary. Classification output explicitly records
`classification_only=true`, `review_completed=false`, and `reviewer_invoked=false`.

The manual evaluator now derives lower bounds from declared/actual changed paths:

- More than 2/6/15 files requires at least S1/S2/S3; more than 40 requires S4,
  or INCONCLUSIVE when the current scope does not allow S4.
- Known line counts above 80/400/1200 require at least S1/S2/S3.
- Two or more known subsystems trigger S3, matching the explicit multiple-subsystems risk rule.
  Numeric capacity ceilings never weaken a semantic risk trigger.
- Test/gate paths require at least S2; structural path tokens such as auth,
  migrations, policy and routing require at least S3. Documentation filenames
  alone do not establish these code-risk triggers.
- A requested lower S level cannot override these facts.
- An unknown fact already covered by the selected deeper review no longer
  creates a redundant escalation or spurious INCONCLUSIVE result.

Line and subsystem counts are optional explicit observations (`--changed-lines`,
`--subsystem-count`), not automatically measured by this CLI. Missing observations
are not proof of small scope. Path heuristics are lower bounds and cannot prove
behavioral safety or S0 eligibility. Caller-reported checks remain caller-reported;
use the existing independent evidence adapter for authoritative verification.

The model-review runner refuses S0, which belongs on the deterministic evidence
path. It checks required reviewer availability, pipeline membership, confirmation,
and the total selected reviewer count before any provider call. An empty or
incomplete catalog cannot produce a completed review. Over-budget or mismatched
selection returns INCONCLUSIVE rather than silently omitting required reviewers.

## Validation record — 2026-09-07

- Coding plan classified through `scripts/prime-code-execute.py`: T3, gated policy
  fingerprint `a814d1b9cc03be53c9a2eaf5919d1775a12846a7bfe37c1392d8714cafbacfd6`.
- Governed dispatch failed before host application because the Windows process
  attempted to resolve `__WSL_WORKSPACE__`. Continued natively under the existing
  infrastructure-failure rule; no lane retry loop or provider change.
- Changed scope: execution router, shared autonomy intent, adaptive-review
  router/CLI/runner, routing regression tests and these documentation pointers.
- WSL pytest: 167 routing/review tests passed, including 46 new boundary cases.
- Additional autonomy, unattended and evidence integration tests: 73 passed,
  one failed. The failure in
  `test_verification_mutation_prepares_candidate_without_promoting` also
  reproduced with HEAD versions of all five changed routing modules loaded in
  memory. It concerns pre-existing learning-candidate ordering, not these edits.
- Tests use deterministic assertions and stub reviewers; no live model reviews
  were required. No latency/token saving percentage has been measured.
- No commit, merge, push, service restart, rollout activation or T2 promotion.

No heuristic router can guarantee perfect task understanding. These guards make
specific inappropriate routes impossible at the checked boundaries and leave
missing evidence, unsupported scope and policy overrides visible.

## Optional Herdr evaluator transport

JSON remains the default review transport. Herdr is an optional, sealed process
host for selected S1-S4 reviewer calls and is currently off. Harness adapters
cannot select it; the evaluator retains S-level selection, reviewer selection,
reconciliation, receipts, and verdict ownership.

| Review path | S-levels | Default | Shipped state |
| --- | --- | --- | --- |
| Deterministic evidence | S0 | Yes for S0 | Active under existing policy |
| JSON reviewer transport | S1-S4 | Yes | Unchanged |
| Herdr reviewer transport | S1-S4 | No | Disconnected, off, unrunnable |

A Herdr process status is not a finding or verdict. Ambiguous delivery is held
without replay, and unknown usage is not returned to the budget. Full mode still
does not grant Herdr verdict or mutation authority. See
[Herdr evaluator provider](../HERDR-EVALUATOR-PROVIDER.md) for the boundary,
probe, kill switch, retention rules, and activation checkpoint.
