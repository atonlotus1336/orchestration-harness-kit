---
name: tree-of-thoughts-method
description: >-
  Tree-of-thoughts reasoning shape: propose K deliberately different next
  steps, evaluate each with boolean flags (not scores), keep survivors, go
  deeper. Use for puzzles, planning, and problems where the first idea is
  often wrong — especially driving small local models. Harness-agnostic:
  file+shell only, no subagents.
metadata: {"hermes":{"tags":["reasoning","search","planning","local-models"],"category":"reasoning-shapes"}}
---

# Tree-of-Thoughts Method

A single chain of reasoning commits to its first step; if that step is wrong,
everything after it is wasted. This shape explores K alternatives per level
and prunes with explicit boolean checks. Cost: ~K× per level. Use only when
wrong-first-idea is the actual failure mode.

## When to use

- Puzzles, constraint problems, plan construction, "find an arrangement
  that satisfies X, Y, Z".
- NOT for questions with one extractable answer (self-consistency-method),
  NOT for judgment calls (ensemble-method), NOT when a solver/tool exists.

## Parameters

K=2 branches per node, depth cap D=4, beam width B=2 (survivors kept per
level). Raise K before raising D — width finds ideas, depth burns tokens.

## Protocol — state lives in `.reasoning-runs/<slug>-tot.md`

1. **Frame:** problem statement + explicit SUCCESS TEST — a check that can be
   run on a candidate solution and answer pass/fail. If you cannot state the
   success test, stop: this shape cannot help (nothing prunable).

2. **Propose.** At each live node, generate K next-steps that are
   SUBSTANTIVELY different (different approach, not different wording).
   Append each as a node:
   ```
   ### node <id> (parent <pid>, depth <d>)
   step: <the proposed next step>
   ```

3. **Evaluate with flags — never scores.** For each new node answer four
   booleans, each with a one-clause reason:
   ```
   flags:
     progress: true|false      # is the goal measurably closer?
     consistent: true|false    # contradicts no constraint or earlier fact?
     complete: true|false      # is this already a full candidate solution?
     dead_end: true|false      # provably cannot reach the goal from here?
   ```
   (1–5 scores flatten to 3s and 4s on small models; a forced boolean with a
   reason does not.)

4. **Prune.** Kill every node with dead_end:true or consistent:false. Keep at
   most B survivors per level (prefer progress:true). Record kills:
   `pruned: <id> — <which flag>`.

5. **Check completion.** Any node with complete:true → run the SUCCESS TEST
   from step 1 on it. Pass → done, write `## solution`. Fail → mark the node
   `dead_end: true` with the test output as reason, continue.

6. **Iterate** propose→evaluate→prune on survivors until solved, all branches
   dead, or depth D. All-dead/depth-out is a REPORTABLE result: write
   `## exhausted` with the best partial and which constraint kept failing —
   that names what to change (K, decomposition, or a bigger model).

## Driving a local model

Propose and evaluate are separate subprocess calls — the evaluator must not
see the proposer's enthusiasm, only the node text:

```bash
ollama run "$MODEL" "Problem+path so far: $(cat node-ctx.txt)
Propose 2 SUBSTANTIVELY different next steps. Number them." > proposals.txt
ollama run "$MODEL" "Constraints: $(cat constraints.txt)
Candidate step: $(cat step1.txt)
Answer exactly 4 lines: progress:/consistent:/complete:/dead_end: true|false — reason" > eval1.txt
```

The tree bookkeeping (prune, beam, depth) is deterministic — you or a
20-line script do it, never the model.

## Failure notes

| Symptom | Fix |
|---|---|
| K proposals are rewordings of one idea | demand a named APPROACH label per proposal; reject duplicates before evaluating |
| Nothing ever gets dead_end:true | evaluator too agreeable — evaluate against the constraint list explicitly, one constraint at a time |
| Solved at depth 1 every time | you didn't need this shape; use a plain chain and save the tokens |
