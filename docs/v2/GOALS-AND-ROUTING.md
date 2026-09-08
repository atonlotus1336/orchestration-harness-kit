# Goals and routing in v2

The shared core now includes finite goal contracts, evidence-backed goalpost
evaluation, and separate execution and review decisions. All harnesses use the
same core. These additions do not activate a provider or create a native goal.

## Execution and review

T0-T4 chooses the executor. S0-S4 chooses review depth. They have no numeric
mapping. The routing.prime.select response includes a separate review object.

T4 requires an explicit autonomous flag or operative user instruction. High-risk,
complex, and multi-system work selects T3 unless the user requested autonomous work.
Quoted examples, discussion, and negative instructions do not activate T4.

Lower lanes need task text, declared paths, and an explicit low risk tier.
T0 and T1 also need a nonempty assertion. The recipient default leaves T2
unpromoted until that installation qualifies it. A qualified host can promote T2
independently. It then executes bounded OMP work. Read actual_lane, policy_override, and
actual_lane_reason. Missing scope selects T3. The core rejects malformed facts.

A request can include a review object with scope, required_level, changed_lines,
subsystem_count, s4_confirmed, and facts. Review uses changed-file and line-count
floors, structural risk, and unknown facts. Asking for a lower level cannot
override those floors. The host supplies the actual changed paths and observed
counts. A classification is caller-reported and does not certify completed review.

S0 uses no reviewer calls. S1-S4 have call ceilings of 1, 3, 5, and 7.
Classification uses no model calls at any level. S4 needs explicit confirmation
or admission through a qualified automatic host path.
The optional review Runner checks required reviewers, source-pinned skills,
pipeline membership, providers, and budgets before a call. It does not come
with a live provider. Existing host approval and cost rules still apply.

## Shared goal contract

Before starting or adopting a goal, define its objective, finite endpoint,
explicit input files, fixed checks, builder identity, and stop limits.
Use modules/orchestration-core/goal-contract.example.json as a template.
Adapt the sample checks to the task before initialization.

Every goal needs at least two quality categories. Human usability is mandatory
for graphical interfaces, command-line output, and documentation. Its threshold
cannot be below 80/100 and it needs independent human or reviewer judgment.
Checks are boolean. Python calculates weighted scores. Models do not assign
numeric grades.

The default checkpoint cadence is 30 active minutes. Baseline, milestone,
failure, and completion checks also apply. Limits default to six goalposts,
two rounds without progress, and four elapsed hours. The weakest categories
guide the next work. The evaluator protects previously passing checks and category scores.

Every observation names a proof artifact, its SHA-256 hash, the current input
fingerprint, method, and passed boolean. Judgment also needs an assessor_id
different from builder_id. Missing, changed, or stale proof is unknown.
These records bind bytes. They do not authenticate a person's identity or prove
the truth of an assessor's claim. The host must collect trustworthy evidence.

## Use the goalpost command

Run from the kit root with Python and the kit dependencies installed:

    python modules/orchestration-core/goalposts.py --workspace <project> init --contract <contract.json>
    python modules/orchestration-core/goalposts.py --workspace <project> fingerprint
    python modules/orchestration-core/goalposts.py --workspace <project> evaluate --observations <observations.json>
    python modules/orchestration-core/goalposts.py --workspace <project> run-work --event-id work-1 -- <authorized-command>
    python modules/orchestration-core/goalposts.py --workspace <project> status

Observation shape, repeated for each check ID:

    {
      "acceptance": {
        "passed": true,
        "artifact": "evidence/test.txt",
        "sha256": "<artifact SHA-256>",
        "input_fingerprint": "<fingerprint command output>",
        "method": "deterministic"
      }
    }

Use method human or independent_review plus assessor_id for judgment checks.
Example values above are a format guide and are not passing evidence.

Exit codes: 0 means continue or complete, 10 means evaluate now, 20 means
iterate, 30 means checkpoint and stop, and 2 means invalid input. Read the
decision field to distinguish completion from permission to continue.
Evaluation also writes a readable Markdown scorecard beside the JSON state.

The host must obey these decisions before dispatching the next work unit.
run-work counts foreground command time and blocks work while evaluation is due.
It checks cadence between commands. It does not interrupt a command at each
30-minute boundary. Native model turns need host timing through record-work.
This kit does not add a background timer or patch native Codex goal internals.

## Continue in another harness

Keep the same goal_id, state path, contract hash, endpoint, rubric, evidence,
work accounting, score floors, and limits. Use one parent writer. The file lock
prevents concurrent writers. Do not reset the goal when switching harnesses.
Existing goals need explicit adoption into a contract. The host does not infer past time.

Stop when the endpoint and every category pass. At a limit or lack of progress,
checkpoint for an explicit revision. Do not widen the endpoint or silently start
another goal. Existing sessions must load this contract before using it.

## Verify

    python modules/orchestration-core/verify-core.py --selftest
    python -m pytest -q installer/tests/test_goalposts.py installer/tests/test_governance_routing.py

Tests use fixtures and local deterministic checks. They do not launch reviewers.

## Bounded T2 qualification (2026-09-08)

T2 is an OMP-only execution lane. It has no Prime model turn. The local runtime
and portable core now share the same execution policy fields and router logic.
The gated default enables T0/T1. It does not enable T2.

T2 requires low risk, one or two exact declared files, and a behavioral host
test (pytest or bun_test) or explicit semantic assertions. Compilation alone
does not qualify. T0/T1 retain precedence when their evidence is sufficient.
Sensitive, complex, autonomous, and insufficiently evidenced tasks retain their
existing deeper routes.

Observed worker requests and tokens include failed proposal attempts. Unknown
usage stays unknown. The host checks the total-token cap before applying a patch.
A T2 host-test failure may escalate once to T3 only after rollback, or after a
semantic assertion fails before application. Safety, scope, budget, unknown
failure codes, and incomplete accounting do not escalate. Provider calls, output
tokens, tool calls, elapsed time, and any explicit total-token cap share one
remaining budget. T2 failures do not enter a second native fallback.

The deterministic evaluate_t2_trials function requires five unique matched live
pairs, failing-before behavioral tests, passing independent post-change tests,
unchanged test oracles, complete usage, and successful results for both lanes.
The existing savings thresholds remain 25% median latency and 30% total-token
reduction. The evaluator makes zero model calls. Recorded JSON is evidence only
when a trusted host produces the originating receipts and trial runner. JSON
alone cannot authenticate an attestation.

The earlier qualification did not pass. In that five-pair batch, four T2 cases
passed and one failed to finalize a proposal. All five T3 comparisons were
blocked before host application. The parent ledger rejected secret-shaped
metadata, so Prime usage was incomplete. That batch supplied no valid savings percentage. The earlier batch omitted the governed-session environment and is
retained as invalid comparison evidence.

That failed batch did not support promotion. The repaired rerun described below
now passes the unchanged gates. Production activation remains a separate change.
When activating a qualified runtime, add T2_OMP_LITE to promoted_lanes while retaining mode=gated.
Apply the same execution_value contract to the kit and set its t2_actual_lane
to T2. Keep recipient installations unpromoted until they qualify their own
runtime. Changing classification does not install an executor or update an
already running session.


## Review admission and order of operations

The shared order is: collect evidence, classify review depth, preflight the
complete reviewer set, admit its budget, invoke bounded reviewers, reconcile
findings, and verify completion. T0–T4 execution depth does not set S0–S4 review
depth. A classification result never counts as a completed review.

Classification accepts `routing_facts`, a closed mapping of boolean observations.
S0 requires `mechanical_or_exact`, `independent_deterministic_validation`,
`no_unknown_load_bearing_fact`, and `no_risk_trigger`, plus measured changed-line
and subsystem counts. Missing values retain an S1 floor. Explicit risk facts
such as `security`, `public_interface`, or `concurrency` raise the review floor.
Arbitrary legacy facts cannot remove these requirements. Caller observations
are classification inputs, not proof that review has passed.

Before the first request, the runner validates every required reviewer, skill
hash, provider, report-only capability, scope hash, and complete context packet.
Required pipeline membership overrides optional catalog metadata. A mismatch
refuses the whole batch. The runner checks source bytes before and after each review.

Use `ReportOnlyProvider(transport=JsonReviewTransport(endpoint, model))` with an
explicitly configured OpenAI-compatible review endpoint. Credentials may be
referenced by environment variable name through `credential_env`. Do not put
credentials in source or URLs. The kit installs and selects no provider by default.
The runner rejects the old unrestricted callback. Attestation alone is insufficient.

One host-owned budget covers the entire reviewer set. It counts actual HTTP
requests and observed input/output/total usage. Full request bytes include the
reviewer lens and source envelope. UTF-8 bytes form a conservative input-token
estimate. Each request also has an output cap of at most 2,048 tokens, bounded by
remaining aggregate output and total allowances. Missing/inconsistent usage is
INCONCLUSIVE. Requests never retry automatically or follow redirects. The client
exposes no model tools, shell, write, or patch channel. A returned tool call is
rejected. A deadline kills and reaps the local HTTP worker. Remote cancellation
and billing remain provider-dependent. A local timeout does not prove remote
compute stopped.

The runtime CLI `run` accepts an S0 PASS only from a finished host-owned checkpoint
receipt via `--workspace`, `--evidence-receipt`, and `--evidence-run-id`, with
`--scope checkpoint` and matching declared paths. The host rechecks current
content, collector/policy identity, lifecycle and scope. Supplying a task packet
also requires its exact hash to match. New risk facts cannot reuse S0 proof.
`--deterministic-pass` remains accepted as a legacy argument but grants no PASS.
Unverified execution is UNKNOWN, and finding totals reflect observed findings.

S1–S4 execution is the `Runner` library interface. The CLI has no reviewer-provider
configuration interface and returns INCONCLUSIVE for those levels. The portable
core exposes classification and the same bounded runner, not the runtime's host
evidence collector. Recipient hosts must supply their own trusted evidence path.

Keep rollout in shadow mode until a real configured review provider is separately
qualified for review quality. Passing local fake-provider tests certifies the
transport controls, not the model's ability to find defects. Fixed phase review
retains authority. S4 requires confirmation or qualified automatic host admission.


## Execution qualification update

The corrected matched T2/T3 batch passed all five pairs with unchanged independent
oracles and complete usage. T2 reduced median elapsed time by 72.8% and total
observed tokens by 54.2% against T3 on these synthetic tasks. Five bounded T4 tasks
also passed and wrote completed durable checkpoints. Earlier failed batches are
retained as historical evidence. These measurements qualify the tested runtime.
They are not universal performance claims or recipient installation evidence.
Production promotion settings were not changed by the qualification run.

## Qualified host and recipient boundaries

The shared completion controller now supports host-qualified automatic S4 scenarios
as described in modules/orchestration-core/lapart_core/review/S4-SCENARIOS.md.
The author qualified supervised Cursor and OpenCode paths with real native sessions,
synthetic source evidence, default host review configuration, invalid-evidence denial
and zero-request completion replay. This does not activate recipient installations.

The portable native_review module includes the same native completion, usage and
release guards as those supervised paths. Recipient hosts must authenticate evidence
and qualify their installed native completion owner. Ordinary GUI/CLI interfaces
cannot inherit the supervisor's qualification by name alone.
