---
name: writing-standard
description: >-
  Anti-slop writing system (ASD-STE100-derived) for docs, runbooks, error
  messages, and agent output templates. Two modes: strict (procedures/errors —
  hard caps, one meaning per word) and flavored (doc prose — discipline without
  the dictionary). Machine-checked by slop-lint.py. NOT for voiced surfaces
  (READMEs, marketing, conversation).
metadata: {"hermes":{"tags":["writing","documentation","clarity","linting"],"category":"writing"}}
---

# Writing Standard — clarity you can lint

Slop is ambiguity with good posture. It is not a feeling; it is six nameable,
machine-checkable habits. This skill bans them where misreading has a cost.
Companion checker: `slop-lint.py` (same dir in the kit; `.claude/scripts/` in
the vault) — zero-token, deterministic, self-testing.

## Pick the mode first

| Mode | Surfaces | Rules |
|---|---|---|
| **STRICT** | procedures, runbook steps, error messages, gate deny-messages, agent output templates | all six bans + instructions ≤20 words + descriptive sentences ≤25 + one meaning per term (define once, reuse exactly) + active voice + imperative for steps |
| **FLAVORED** | general technical prose, design notes, reports | the six bans + ≤25-word sentences; vocabulary stays free |
| **EXEMPT** | READMEs (voiced by kit convention), marketing copy, conversation, anything needing a voice | do not apply this skill — "a torque-wrench spec applied to a poem" |

Register notes: **caveman mode optimizes tokens; this optimizes ambiguity.**
Caveman may drop articles and use fragments — never do that in STRICT mode;
an instruction must be complete and unambiguous even if longer.

## The six banned habits (with fixes)

1. **Synonym rotation** — one thing, one name, entire document.
   BAD: "the user… the customer… the client" (same person?).
   FIX: pick one term at first mention; reuse it verbatim.
2. **Hedge stacks** — helper verbs doing zero work.
   BAD: "it's important to note that this may potentially help to improve".
   FIX: state the fact or the action. "This reduces cache misses."
3. **Frozen verbs (nominalization)** — verbs hiding inside nouns.
   BAD: "perform an analysis of", "provides assistance".
   FIX: "analyze", "helps".
4. **Marketing adjectives** — claiming quality instead of showing it.
   BAD: seamless, robust, powerful, cutting-edge, effortless.
   FIX: delete, or replace with the measurable fact.
5. **Run-ons** — several ideas welded with em-dashes/semicolons.
   BAD: four clauses, one sentence. FIX: four sentences. Semicolons are
   banned in STRICT mode.
6. **Soft phrasal verbs** — chatty two-word verbs with exact one-word
   replacements. BAD: spin up, reach out, dive into. FIX: start, contact,
   examine. ("You remove the panel. You do not take it off.")

Plus: prefer **active voice** ("the installer deletes X", not "X is
deleted") — passives hide the actor, and in a runbook the actor is the
instruction.

## Workflow (draft → lint → fix → relint)

1. Load this skill BEFORE drafting — prevention beats correction (measured:
   a loaded system cuts slop 50–74% vs fixing afterward).
2. Draft in the chosen mode.
3. Run the linter: `python slop-lint.py <file>` (add `--strict` for STRICT
   surfaces). It prints violations-per-100-words + line-numbered flags.
4. Fix ONLY the flagged lines. Rerun until under threshold (default 2.0;
   STRICT surfaces should reach ≤1.0).
5. Gate use: `--max 1.0` makes the exit code enforce the threshold.

## The honest limits

- The linter fixes FORM, not substance. It can polish a hollow paragraph
  into a clean hollow paragraph. Truth is doc-drift-audit's job.
- Regex passive-detection is approximate; treat that category as advisory.
- Over-simplifying can backfire (Airbus finding): shorter is not always
  clearer. The caps are ceilings, not targets.
