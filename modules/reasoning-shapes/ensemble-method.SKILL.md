---
name: ensemble-method
description: >-
  Ensemble reasoning shape: persona-diverse voters (analytical, skeptical,
  pragmatic) answer independently, then an aggregation pass selects or
  synthesizes. Use when failure modes are diverse — judgment calls, reviews,
  risk assessment — especially driving small local models. Harness-agnostic:
  file+shell only, no subagents.
metadata: {"hermes":{"tags":["reasoning","reliability","local-models","voting","personas"],"category":"reasoning-shapes"}}
---

# Ensemble Method

Self-consistency repeats the SAME reasoning style; an ensemble varies the
LENS. Use it when a question can fail in different ways — a plan that is
technically sound but impractical, a claim that is plausible but unsourced.
Diversity catches what redundancy cannot.

## When to use

- Judgment calls, plan reviews, risk assessments, "should we X?" questions.
- Claims where you want a skeptic explicitly hunting for the flaw.
- NOT for single-extractable-answer arithmetic (self-consistency-method is
  cheaper and better there).

## Protocol

1. **Frame** in `.reasoning-runs/<slug>.md`: question + the voter roster.
   Default roster (adapt to task):
   - **analytical** — decompose, check each part against known facts.
   - **skeptical** — assume the popular answer is wrong; hunt counterexamples.
   - **pragmatic** — what actually works in THIS environment, constraints first.

2. **Vote independently.** Each voter answers from its lens ONLY, blind to the
   others (write before reading, or separate subprocess calls). Each ends
   with two lines:
   ```
   POSITION: <one sentence>
   CONFIDENT: true|false
   ```
   Boolean confidence, never a 1–10 score — models flatten numeric scales to
   the band middle.

3. **Aggregate.** One final pass reads all positions:
   - All agree → adopt, note unanimity.
   - Split → the aggregation names the disagreement, weighs CONFIDENT:true
     positions over false, and picks or synthesizes — recording WHY the
     losing lens lost (its concern is answered, out of scope, or accepted
     as a known risk).
   - Skeptic alone dissents with CONFIDENT:true → do not steamroll it:
     answer its specific counterexample or adopt its position.

4. **Record** `## decision` with position, dissents, and disposition of each
   dissent. A dissent with no disposition is an unfinished aggregation.

## Driving a local model

```bash
MODEL=qwen3:4b
for persona in analytical skeptical pragmatic; do
  ollama run "$MODEL" "You are the $persona reviewer. $(cat lens-$persona.txt)
$(cat question.txt)
End with exactly: POSITION: <one sentence> / CONFIDENT: true|false" \
    > ".reasoning-runs/${SLUG}-$persona.txt"
done
```

Aggregation can be the driving model or a fourth subprocess call fed the
three position files.

## Failure notes

| Symptom | Fix |
|---|---|
| Voters converge on identical text | personas not reaching the prompt — put the lens FIRST, before the question |
| Aggregator always sides with majority | require it to quote each dissent's strongest sentence before ruling |
| All CONFIDENT:false | the question lacks decidable substance at this model size — decompose or escalate |
