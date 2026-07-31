---
name: lats-method
description: >-
  LATS (Language Agent Tree Search) reasoning shape: tree search over ACTIONS
  with tool feedback — select the most promising leaf by visit/pass counts,
  expand, evaluate with boolean flags, propagate results up the tree. Use for
  hard multi-step tasks where actions have checkable outcomes (code, debugging,
  search). Harness-agnostic: file+shell only, no subagents.
metadata: {"hermes":{"tags":["reasoning","search","tool-use","local-models"],"category":"reasoning-shapes"}}
---

# LATS Method

Tree-of-thoughts searches over REASONING; LATS searches over ACTIONS whose
results you can actually check (a command runs, a test passes, a file
contains the string). Real feedback replaces guessed evaluation — that is
the entire advantage. The costliest shape here; use it last.

## When to use

- Multi-step tasks with checkable intermediate outcomes: fixing a failing
  test, building a query/config that must validate, multi-hop search.
- NOT when steps can't be checked until the very end (use
  tree-of-thoughts-method), NOT for anything a straight ReAct loop solves in
  a few steps — try that first.

## Parameters

K=2 candidate actions per expansion, max E=8 expansions total. Selection is
deterministic — no model call.

## Protocol — state lives in `.reasoning-runs/<slug>-lats.md`

1. **Frame:** goal + SUCCESS TEST (a runnable check, e.g. "pytest exits 0").
   Root node = the initial state, `visits: 0, passes: 0`.

2. **Select** (deterministic): walk from root, at each level pick the child
   with the highest pass-rate `passes/visits`; break ties toward FEWER visits
   (cheap exploration bonus, no UCB math for a weak model to fumble). Stop at
   a leaf that is not exhausted.

3. **Expand:** propose K substantively different next ACTIONS from the leaf.
   An action must be executable: a command, an edit, a query — not "think
   about X".

4. **Act + evaluate:** execute each action (respect the dry-run gate for
   anything destructive — reasoning shapes get no exemption). Record real
   output, then flags:
   ```
   ### node <id> (parent <pid>)
   action: <what was run>
   observation: <trimmed real output>
   flags:
     progressed: true|false   # observation moved toward the goal?
     goal_met: true|false     # SUCCESS TEST passes now?
     dead: true|false         # this line provably can't reach the goal
   ```

5. **Backpropagate** (deterministic): for the new node and every ancestor:
   `visits += 1`; `passes += 1` if progressed. dead:true → node ineligible
   for selection forever.

6. **Loop** select→expand→act→backprop until goal_met (run the SUCCESS TEST
   once more, record its output as evidence — the flag alone is narration,
   the test output is proof) or E expansions spent. Budget-out is
   reportable: `## exhausted` + best pass-rate path + what kept failing.

## Driving a local model

The model does ONLY steps 3 (propose) and the flag half of 4. Selection,
execution, bookkeeping, and backprop are yours or a script's:

```bash
ollama run "$MODEL" "Goal: $(cat goal.txt). State: $(cat leaf-ctx.txt)
Propose 2 different concrete next ACTIONS (runnable commands or edits)." > actions.txt
# execute yourself, then:
ollama run "$MODEL" "Goal: $(cat goal.txt). Action taken: ... Output: $(cat obs.txt)
Answer 3 lines: progressed:/goal_met:/dead: true|false — reason" > flags.txt
```

## Failure notes

| Symptom | Fix |
|---|---|
| Search keeps re-expanding one branch | tie-break toward fewer visits is missing — re-check step 2 |
| goal_met:true but SUCCESS TEST fails | the flag is narration; always re-run the test and trust its output (evidence outranks narration) |
| All actions are variations of the first | require a named strategy label per action; reject same-label pairs |
| 8 expansions, no progress | wrong tool for the task — decompose the goal or drop to plain ReAct with a bigger model |
