---
name: self-consistency-method
description: >-
  Self-consistency reasoning shape: N independent blind attempts at the same
  question, majority vote on the final answer, disagreement report. Use for
  factual/calculation/decision questions where a single pass is unreliable —
  especially when driving a small local model. Harness-agnostic: file+shell
  only, no subagents.
metadata: {"hermes":{"tags":["reasoning","reliability","local-models","voting"],"category":"reasoning-shapes"}}
---

# Self-Consistency Method

Weak models fail differently on each sample; the majority answer survives
errors that any single pass would ship. This shape buys reliability with
repetition. Cost: N× tokens. Use N=3 default, N=5 for high-stakes.

## When to use

- Questions with ONE extractable final answer (number, name, choice, yes/no).
- The model is small/local, or the single-pass answer keeps flip-flopping.
- NOT for open-ended prose (use ensemble-method's synthesis instead), NOT
  when a deterministic tool can answer (calculator, grep — always prefer it).

## Protocol

1. **Frame.** Write the question and the answer format to the run file
   `.reasoning-runs/<slug>.md` (create the dir if absent):

   ```
   # self-consistency: <slug>
   QUESTION: <exact question>
   ANSWER-FORMAT: <e.g. "single integer">
   N: 3
   ## attempts
   ```

2. **Attempt, blind, N times.** For each attempt i (1..N):
   - Reason step by step from the QUESTION ONLY. Do NOT read prior attempts —
     independence is the whole mechanism. (Self-driven: write each attempt
     immediately, look back only at the vote stage. Subprocess-driven:
     independence is automatic — see below.)
   - Append to the run file: `### attempt i` + reasoning + `FINAL: <answer>`.

3. **Vote.** Read all FINAL lines. Normalize (trim, case, units). Majority
   wins. Record:

   ```
   ## vote
   answers: [a1, a2, a3]
   winner: <answer>  votes: <k>/<N>
   unanimous: true|false
   ```

4. **Disagreement report.** If not unanimous: name WHERE the losing attempts
   diverged (the first step that differs), append as `## disagreement`. A
   2/3 split on a calculation usually means one attempt slipped an operation
   — the divergence step tells you whether the majority is trustworthy.
5. **No majority (all different):** do NOT pick one. Report the spread and
   either raise N by 2 or decompose the question — an even spread means the
   question is too hard for this model at this grain.

## Driving a local model (weaning configuration)

```bash
MODEL=qwen3:4b   # whatever `ollama list` shows
for i in 1 2 3; do
  ollama run "$MODEL" "$(cat question.txt) Think step by step, then output exactly one line: FINAL: <answer>" \
    >> ".reasoning-runs/${SLUG}-attempt-$i.txt"
done
grep -h "FINAL:" .reasoning-runs/${SLUG}-attempt-*.txt
```

Then vote (step 3) yourself — the vote is deterministic bookkeeping, not a
model call.

## Failure notes

| Symptom | Fix |
|---|---|
| All N attempts identical word-for-word | temperature too low / model caching; vary phrasing of the reasoning instruction, not the question |
| Majority is wrong anyway | correlated failure — the model lacks the knowledge; repetition cannot create it. Escalate to retrieval or a bigger model |
| Answers won't normalize (units, phrasing) | tighten ANSWER-FORMAT and rerun; never hand-map divergent answers onto each other |
