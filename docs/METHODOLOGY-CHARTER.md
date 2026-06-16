# MyVault Methodology Charter

Purpose: give Codex, Hermes, and Claude a shared method for reasoning,
implementation, and answer construction after authority and retrieval have
oriented the task.

This is not a replacement for the authority constitution, retrieval playbook,
or master index. It is the process layer:

- `AUTHORITY-CONSTITUTION.md` decides precedence.
- `RETRIEVAL-PLAYBOOK.md` decides where to look.
- `MASTER-INDEX.md` decides which skill or agent to load.
- `METHODOLOGY-CHARTER.md` decides how to proceed once the task is routed.

If this charter conflicts with runtime policy, the current user command,
project law, or direct evidence, apply the authority constitution first.

## Core Method

Use this loop for non-trivial work:

1. Orient.
   - Identify the user goal, active project, relevant authority layer, and
     likely evidence source.
   - Use indexes and MCP/RAG routers before broad reading.

2. Constrain.
   - State the real constraints: user intent, tool limits, canonical paths,
     existing system shape, time/cost risk, and evidence gaps.
   - Separate what is known from what is inferred.

3. Choose the smallest useful path.
   - Prefer one well-routed source over many loosely related sources.
   - Prefer existing local patterns over new abstractions.
   - Prefer reversible, incremental edits over large speculative rewrites.

4. Execute.
   - Make the narrowest change that satisfies the goal.
   - Keep canonical ownership boundaries intact.
   - Avoid duplicating active sources of truth.

5. Verify.
   - Check the result with the cheapest reliable evidence: tests, parsers,
     health checks, direct reads, MCP status, screenshots, or command output.
   - Treat failed or missing verification as a fact to report, not as a
     narrative problem to smooth over.

6. Record only durable learning.
   - Update docs, indexes, manifests, decision journals, or memory only when
     the update prevents future wrong work.
   - Do not turn ordinary task progress into permanent doctrine.

## Reasoning Defaults

- Evidence before narration.
- Specific before general.
- Live state before memory.
- Source reads before summaries when the exact source is practical.
- Current user intent before stale handoffs.
- Smallest sufficient context before broad context loading.
- Explicit tradeoffs before hidden assumptions.
- Diagnosis, planning, execution, and verification should stay distinct unless
  the task is trivial.
- When uncertain, name the uncertainty and the next smallest check.
- Dial reasoning depth by stakes — see `REASONING-EFFORT.md` (Claude `think`/`ultrathink`,
  Codex effort profiles, Hermes model choice). Spend depth on high-stakes work, not trivia.

## Build Defaults

- Read local patterns before inventing a new one.
- Preserve canonical roots:
  - Callable Codex assets: `C:\Users\alice\.codex`.
  - MyVault vault, MCP, RAG, memory, archives, and project knowledge:
    `D:\MyVault`.
- Add abstractions only when they remove real complexity, reduce meaningful
  duplication, or match an established local pattern.
- Prefer structured APIs, schemas, and parsers over ad hoc string handling when
  they are available.
- Keep edits close to the requested behavior and ownership boundary.
- Verify shared orchestration changes with both file-level checks and runtime
  checks when practical.
- Do not optimize by adding always-loaded text. Optimize by improving routing,
  indexes, startup hints, and narrow source selection.

## Retrieval Defaults

Routing lives in `RETRIEVAL-PLAYBOOK.md` (route table + numbered steps); evidence
precedence in `AUTHORITY-CONSTITUTION.md` (memory = lead, evidence = proof). Not
restated here — use the smallest router that answers; broad `rg` / vault walks /
re-indexing are escalation paths, not defaults.

## Answer Defaults

- Answer from the evidence gathered, not from the amount of context loaded.
- For non-trivial work, name the route used and the evidence boundary.
- Keep summaries compressed and cite exact files, commands, notes, or MCP
  results when practical.
- Do not paste long source text unless the user explicitly needs a quote.
- If the answer depends on an assumption, label it as an assumption.
- If a tool fails, distinguish tool failure from evidence absence.

## Edge Case Steering

- Conflicting docs: apply `AUTHORITY-CONSTITUTION.md`, cite the conflict, then
  use the narrowest compliant action.
- Broad audit request: start with indexes, status endpoints, manifests, and
  configured roots before walking the whole vault.
- Stale session: reload MCP, restart the session, or re-read direct files before
  judging the system state.
- New machine setup: use `ORCHESTRATION-LAYER.md` and
  `D:\MyVault\.claude\LLM-OPS-RUNBOOK.md` (step-by-step, any-model), then run
  `D:\MyVault\.mcp-servers\audit-orchestration.ps1`.
- User asks for philosophy or wide guidance: use this charter to frame method,
  but keep actionable next steps tied to evidence.

## Anti-Patterns

- Loading every related note before making a simple decision.
- Treating RAG output as final proof when the cited source can be read.
- Replacing a working local convention with a generic best practice.
- Letting memory override current files, commands, or MCP status.
- Creating duplicate config roots because one client is temporarily failing.
- Writing permanent doctrine for one-off preferences or ordinary bug fixes.

## One-Prompt Version

Apply authority first, route before reading, then work in the smallest useful
loop: orient, constrain, execute, verify, and record only durable learning.
Evidence beats narration; live state beats memory; indexes and MCP/RAG routers
beat broad scans. Preserve canonical roots, follow local patterns, and report
uncertainty or verification gaps plainly.
