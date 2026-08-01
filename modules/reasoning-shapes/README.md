# reasoning-shapes — reliability techniques any model can follow

**What they are:** four step-by-step reasoning procedures written as skill files.
Each one is a "shape" — a structure that forces a model to work more carefully than
a single stream of text. No code, no dependencies: they're documents a model reads
and follows.

**Why you'd want them:** big frontier models are careful by default; small local
models aren't. These shapes move the reliability *into the procedure*, so a
7B–8B local model can be trusted with real work. Tested result on an 8B model:
with the vote-and-refuse shape, every answer it committed to was correct, and it
correctly *refused* on exactly the questions it would have gotten wrong.

## The four shapes (use the smallest that works)

1. **`self-consistency-method`** — answer the same question 3× independently, then
   vote; no majority → refuse. For facts and calculations.
2. **`ensemble-method`** — three personas (analytical, skeptical, pragmatic) answer
   separately, then reconcile. For judgment calls and reviews.
3. **`tree-of-thoughts-method`** — propose several next steps, prune with yes/no
   checks, go deeper. For puzzles and planning where the first idea is often wrong.
4. **`lats-method`** — tree search over *actions with checkable results* (does the
   test pass?). The most expensive; use it last.

## Install just this part

Copy the `.SKILL.md` files into wherever your harness looks for skills
(`~/.claude/skills/<name>/SKILL.md` for Claude Code, your skills dir for
Codex/Hermes/OpenCode) — or just paste a shape's protocol into a prompt. Each file
documents how to drive a **local Ollama model** through the shape with plain
`ollama run` calls, which is the whole point: reliability that survives without a
frontier model.
