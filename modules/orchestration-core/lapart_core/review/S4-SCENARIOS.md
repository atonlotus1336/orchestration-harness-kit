# Conditional S4 scenario rules


The pure s4_scenarios.recommend_s4 classifier defines candidate rules. It makes
zero model calls, performs no dispatch, and grants no execution authorization.
Candidate rules do not activate recipient runtimes. S4 execution still requires
explicit confirmation until a host execution path passes qualification.

| Scenario | Required evidence |
|---|---|
| Milestone system gate | A defined milestone reaches a system review already required by the fixed goal contract. |
| Final system gate | A pre-completion boundary reaches that contract's required final system review. |
| Material architecture change | Fresh changed-source evidence identifies at least two structural files across at least three affected subsystems. |
| Goalpost cross-system weakness | An active-work interval is due. Independent evidence shows a remaining weakness across at least two affected subsystems. |

Timer expiry alone, an ordinary milestone, one risky filename, a high T lane or
an unproven reviewer suggestion does not qualify. A satisfied/closed goal stays
closed. S4 must never add new endpoint requirements or lower usability thresholds.

The host must establish a fixed endpoint, open goal, safe work boundary, current
verified source, declared scope, no explicit Later/Skip, no exhausted loop, and
no completed or pending S4 for that source. Missing evidence defers the candidate.
The complete S4 budget envelope must fit the remaining shared task budget. These
rules do not mint a separate budget or reset existing use. A native hook can impose
a shorter execution deadline, such as 240 seconds, within the reserved S4 ceiling.
Deadline exhaustion returns incomplete. It never grants a pass or a fresh allowance.

Outputs are CANDIDATE, NOT_NEEDED, DEFERRED or CHECKPOINT. CANDIDATE is a
recommendation, never a review PASS or an authorization. Its key binds goal ID,
contract hash and source fingerprint across events/harness handoffs. A host must
persist and claim that key atomically, verify observation provenance, reserve the
budget, honor cancellation and qualify end-to-end dispatch before enabling
automatic S4. Dictionary fields alone are not proof. Existing source-bound
review receipts remain the completion authority.

Run python -m pytest -q installer/tests/test_s4_scenarios.py to verify the
candidate and denial boundaries without calling a model.


## Host admission and execution

The shared s4_dispatch module implements host admission and an atomic signed claim
for the goal contract and current source. It requires an enforced harness listed
in automatic_s4.enabled_harnesses. Custom goal states must be explicitly bound
through automatic_s4.goal_states using the resolved workspace and a project-local
state path. The host never crawls a project to discover goal files.

The host verifies execution evidence, goal freshness, structural scope and remaining
lineage budget before dispatch. The host checks source and goal state again afterward.
A completed claim reuses its result. Pending, interrupted, stale or invalid claims
cannot spend again. Configuration or implementation changes invalidate reuse.
Required S4 work that cannot fit the existing budget must hold completion. The host
must not substitute a cached shallower review. Native cancellation must terminate
the hook and preserve the pending claim for inspection.

The runtime host adapter supplies the verified evidence collector and calls this
library. These library files do not install native hooks or activate recipients.
The local developer environment has qualified native and supervised automatic architecture
reviews. Each recipient must qualify its own native dispatcher and provider.
Goalpost scenarios additionally require a registered, fresh goal state.
