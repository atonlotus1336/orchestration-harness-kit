# docs — the operating doctrine and the install guide

**What this is:** the written rules that make a multi-agent setup behave
consistently — the part of the stack that isn't code at all. Load them into any
harness (via CLAUDE.md / AGENTS.md pointers) and every agent you run inherits the
same discipline.

Small local open-source models should begin with
[`SMALL-MODEL-START.md`](SMALL-MODEL-START.md). It reduces installation to one
command and one pass/fail flag at a time.

## Reading order for newcomers

1. **`REPLICATION-GUIDE.md`** — the full-stack install, start here if you're setting up.
2. **`AUTHORITY-CONSTITUTION.md`** — the crown jewel. How an agent should resolve
   conflicts between what it's told, what it remembers, and what it observes.
   One line captures it: *memory is a lead, not proof; evidence outranks narration.*
3. **`RETRIEVAL-PLAYBOOK.md`** — route before reading: how to answer questions from
   the cheapest sufficient source, plus the KEEP/DROP chunk-grading protocol that
   stops RAG from polluting answers with almost-relevant text.
4. **`METHODOLOGY-CHARTER.md`** — how agents should plan, build, and verify.
5. **`REASONING-EFFORT.md`** — when to think hard vs. answer fast, per harness.
6. **`LLM-OPS-RUNBOOK.md`** + **`ORCHESTRATION-LAYER.md`** + **`CONNECTOR.md`** —
   operations references for the full stack: recovery procedures, architecture,
   and MCP wiring details. Consult when things break, not bedtime reading.

## Install just this part

There's nothing to run. Copy the docs somewhere stable, then point your harness at
them — e.g. in your `CLAUDE.md` or `AGENTS.md`: "For conflict resolution follow
AUTHORITY-CONSTITUTION.md; for retrieval follow RETRIEVAL-PLAYBOOK.md." Agents
read them on demand. Even adopting the constitution alone measurably changes how
an agent handles stale memories and confident-but-wrong recall.
