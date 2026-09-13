---
name: evidence-integrity-audit
description: >-
  Audit whether gates, tests, evals, and closure evidence actually measure anything. Use before accepting a phase/milestone as done, or when reviewing 'all checks pass' claims. Hard rule: a gate that cannot name an input that would make it FAIL is void.
---

# Evidence Integrity Audit

A gate exists to stop bad work. Evidence exists to show the work is good.
Both can be faked *by accident* — a well-meaning author writes a check that
compares a value to itself, seeds a table so the count is nonzero, or lets
the thing being graded write its own grade. The pass is green, and it means
nothing. This skill is the method for catching that before closure.

**The one hard rule (apply it to every gate):**

> For every gate, state a concrete input or state that would make it FAIL.
> If no such input exists — or the only failing input is "the file was
> deleted" — the gate is void and counts as NO evidence.

## The audit procedure

### Step 1 — Enumerate

List every gate/check/eval the closure claim rests on. For each, record:
`id | what it CLAIMS to verify | mechanism (command/script/query) |
data source (real / synthetic / hand-authored) | who wrote the expected value`.
If you can't fill a column by reading the artifact, the gate is UNKNOWN —
audit it first.

### Step 2 — Classify against the taxonomy

Check each gate against every pattern. One match = the gate is suspect;
name the pattern in the verdict.

| # | Pattern | Signature | Litmus question |
|---|---|---|---|
| 1 | **Tautology** | expected == requested == result; the check compares a value to its own source ("assert config.port == config.port"; gate reads the ledger entry the same run just wrote and compares it to itself) | Do the two sides of the comparison have independent origins? |
| 2 | **Self-grading** | the system being evaluated produced the labels, the expected outputs, or the rubric it is scored against | Could the grader disagree with the author? Are grader and author the same process/model/person-in-same-sitting? |
| 3 | **Existence-as-validation** | check passes because a file/row/endpoint EXISTS, while claiming the content is CORRECT ("ledger has 12 rows" ≠ "12 phases verified") | Does the check read the content, or just find the container? |
| 4 | **Synthetic-as-signal** | seed rows, fixture data, or demo entries counted in a metric that claims to measure real behavior | If all real activity stopped, would this number stay green? |
| 5 | **Vacuous pass** | the check runs against nothing: empty test set, zero matching files, filter that excludes everything, try/except around the assertion | What was the n? A pass with n=0 is not a pass — require the gate to fail on empty input. |
| 6 | **Unfalsifiable gate** | acceptance criteria phrased so no observable state fails it ("works correctly", "is robust", "handles errors appropriately") | Apply the hard rule — write the failing input. Can't? Void. |
| 7 | **Proxy drift** | the check measures something adjacent to the claim (port open ≠ service healthy; compiles ≠ runs; HTTP 200 ≠ correct body) | Trace claim → measurement: what real-world difference would the metric miss? |

Patterns 3/5/7 correspond to the *Phantom Taxonomy* in
`claudex-passover-loops` (Vacuous Pass, Hollow Interface, Inert Compile) —
same disease, phase-boundary scope. This skill is the general, portable
form; when running passover loops, cite that skill's constraint mechanism
rather than re-deriving it.

### Step 3 — Falsification test (the decisive step)

For every gate still standing, do one of:

- **State the failing input** and, where cheap, ACTUALLY INJECT IT: break
  the thing deliberately (wrong port, corrupted row, flipped label, empty
  table) and confirm the gate goes red. A gate never seen red is unproven.
- If injection is too expensive/destructive, do a **paper falsification**:
  write the failing state precisely and trace, line by line through the
  gate's mechanism, why it would fail. Vague trace = suspect gate.

Record: `gate | failing input | verified-red? (injected / paper / NO)`.

### Step 4 — Verdict table

```
GATE AUDIT — <subject> — <date>
| gate | claims | pattern hits | falsifiable? | verdict |
|------|--------|--------------|--------------|---------|
verdicts: SOUND | WEAK (counts as partial evidence, name why) | VOID
CLOSURE IMPACT: <can the milestone/claim still close? what must be fixed first>
```

A closure claim is only as strong as its weakest LOAD-BEARING gate: state
which gates are load-bearing (the claim fails without them) vs corroborating.
One VOID load-bearing gate = closure blocked, regardless of how many others
pass.

## Rules of engagement

- Audit the GATE, not the author. Every pattern here is usually an honest
  mistake; write verdicts so they're fixable, not shameful.
- Never fix a gate silently during the audit — report first, fix as a
  separate step, then re-run the falsification test on the fixed gate.
- Distrust green history: a gate that has passed 50 times and never once
  failed is a candidate for patterns 1/5/6, not a track record.
- Real data beats synthetic in every verdict; synthetic data is acceptable
  only when labeled as such AND the metric's claim is scoped to it.

## Troubleshooting (small models)

| Symptom | Cause | Fix |
|---|---|---|
| Can't tell if a comparison is tautological | origins not traced | for each side of the `==`, write WHERE the value was produced (file, process, person). Same origin → pattern 1 |
| Gate is a script you don't understand | auditing the description, not the mechanism | read the script itself; the description is a claim, not evidence. If the script is unreadable, verdict UNKNOWN, never SOUND |
| Everything looks fine after Step 2 | classification done from memory of the gate, not the artifact | re-do Step 2 with the gate's actual code/config open; quote the exact line that performs the check in each verdict |
| Falsification test feels unsafe to run | injection could damage real state | use paper falsification (Step 3) — never inject into production data or live user state; work on a copy |
| "The tests pass" is offered as the evidence | proxy drift — tests measure what tests measure | ask for the gate list (Step 1); tests are gates too and get the same taxonomy |
| Too many gates to audit | no prioritization | audit load-bearing gates first (the ones the closure claim cannot survive without); corroborating gates can be sampled |
| Pressure to pass because deadline | role confusion | your output is the verdict table, not the closure decision; report VOID findings plainly and let the owner decide |
| You wrote the gates yourself earlier | self-grading (pattern 2) applies to YOU | say so in the report and recommend an independent pass (fresh session/model) for the load-bearing gates |
