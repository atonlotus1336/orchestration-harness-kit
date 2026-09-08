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
T0 and T1 also need a nonempty assertion. T2 stays recommended-only and executes
as T3 under the gated policy. Read actual_lane, policy_override, and
actual_lane_reason. Missing scope selects T3. The core rejects malformed facts.

A request can include a review object with scope, required_level, changed_lines,
subsystem_count, s4_confirmed, and facts. Review uses changed-file and line-count
floors, structural risk, and unknown facts. Asking for a lower level cannot
override those floors. The host supplies the actual changed paths and observed
counts. A classification is caller-reported and does not certify completed review.

S0 uses no reviewer calls. S1-S4 have call ceilings of 1, 3, 5, and 7.
Classification uses no model calls at any level. S4 needs explicit confirmation.
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
