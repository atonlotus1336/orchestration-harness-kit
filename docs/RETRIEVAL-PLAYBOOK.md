# Retrieval Playbook

Purpose: minimize context loading by routing through indexes and RAG before
opening source files. Use this when a task needs a skill, agent, document,
note, project memory, or graph relationship.

> If a RAG/MCP/memory backend seems broken (not just a routing question), first run `D:\MyVault\.claude\LLM-TRIAGE-CARD.md` (symptom->action), which routes into `LLM-OPS-RUNBOOK.md`.

Authority source: apply `D:\MyVault\.claude\AUTHORITY-CONSTITUTION.md` before
resolving conflicts between current user intent, operating rules, project law,
evidence, and memory.

Methodology source: after authority and retrieval routing, use
`D:\MyVault\.claude\METHODOLOGY-CHARTER.md` when the task needs reasoning,
implementation, answer-structure, or edge-case process guidance.

## Retrieval Contract

Do not scan the vault or skill folders broadly. Route first, then read the
smallest sufficient source.

## Route table — request type → exact tool (copy-paste, no inference)

| Request | Exact call |
|---|---|
| Find / pick a skill | `MASTER-INDEX.md` → keyword→category table at top → load ONE `SKILL.md`. Semantic fallback: run `query-skills-rag.py "<intent>" 5` |
| Prior decision / session memory | `memory_recall` or `memory_stub_list` (Memoryweaver MCP), else `mcp__rag-orchestrator__rag_query` |
| Semantic doc search (vault) | `mcp__rag-orchestrator__rag_query` (limit 5) |
| Exact note by title/path | `mcp__obsidian__search_notes` → `mcp__obsidian__read_notes` |
| Entity / relationship question | `mcp__rag-orchestrator__rag_query` (routes to GraphRAG) |

Standardize on **limit 5**. Weak clients: do NOT introspect your own tool params — just call `mcp__rag-orchestrator__rag_query`. Backends behind the orchestrator: Memoryweaver = past decisions/sessions · GraphRAG = vault-wide relationships. Use Obsidian or local-rag for current vault documents.

1. Classify the request.
   - Skill or agent procedure: use the skills pipeline.
   - Project/document knowledge: use RAG or Obsidian.
   - Prior decisions/session memory: use Memoryweaver first.
   - Relationship or architecture graph: use GraphRAG or Graphify.
   - Exact note title/path: use Obsidian search/read.

2. Skills and agents.
   - Start with `D:\MyVault\.claude\MASTER-INDEX.md`.
   - Pick a category, then compare tags and one-line descriptions.
   - Load only the selected `SKILL.md` or agent definition.
   - If uncertain, compare at most three candidates.
   - For skill-set **coordination** (which sets hand off to which, where a new
     skill belongs in the tree) load `D:\MyVault\.claude\SKILL-INTEGRATION.md`
     (read-on-demand registry + handoff graph). MASTER-INDEX finds a skill;
     SKILL-INTEGRATION chains skill-sets.
   - If still uncertain, use semantic fallback against `skills_index`:
     - Direct `local-rag` only when the client exposes collection selection
       (a `collection` parameter on its query tool); most clients do not —
       an unqualified `local-rag` query hits the general document corpus,
       not the skill index.
     - Otherwise `rag-orchestrator` with a skill-focused query
       (`skill <intent> <domain> <tool>`), or the Direct Chroma Check below.
   - Skip `[CC+Cx]` skills in Hermes unless adapting them manually.

3. Documents and notes.
   - Exact title or path: use Obsidian MCP search/read.
   - Broad semantic question: use `mcp__rag-orchestrator__rag_query` with a small limit (5).
   - Backend-specific question:
     - Memory/session facts: Memoryweaver.
     - Current vault docs: Obsidian exact reads or local-rag.
     - Community/entity relationships: GraphRAG.
   - Read only cited chunks or the smallest cited source section.

4. Codebase and architecture relationships.
   - Use existing `graphify-out/graph.json` if present.
   - Use Graphify for local code/document relationship traversal.
   - Use GraphRAG for vault-level entity/community questions.

5. Direct Chroma Check (`skills_index`).
   - Canonical procedure when no connected client exposes collection-aware
     search. Store: `D:\MyVault\.rag\db`, collection `skills_index`.
   - `[PowerShell] python D:\MyVault\.claude\scripts\query-skills-rag.py "<intent>" 5`
   - `[WSL/bash] python3 /mnt/d/MyVault/.claude/scripts/query-skills-rag.py "<intent>" 5`

6. Backend health and setup questions.
   - Route live-status, wiring, and troubleshooting questions to
     `D:\MyVault\ORCHESTRATION-LAYER.md` and the read-only audit:
     `D:\MyVault\.mcp-servers\audit-orchestration.ps1`.
   - Step-by-step recovery procedures for any harness:
     `D:\MyVault\.claude\LLM-OPS-RUNBOOK.md`.

7. Staleness checks.
   - If a newer `SKILL.md` exists than `MASTER-INDEX.md`, rebuild:
     `python D:\MyVault\.claude\scripts\build-skills-manifest.py`
   - Then refresh skill semantic search:
     `python D:\MyVault\.claude\scripts\index-skills-rag.py`
   - Do not auto-run GraphRAG indexing unless explicitly requested; it can be long-running.

8. Answer discipline.
   - State which route was used: index, Obsidian, local-rag, Memoryweaver, GraphRAG, or Graphify.
   - Cite the exact file or note read when practical.
   - If retrieval is stale or ambiguous, say so and name the next smallest check.
   - If evidence conflicts with narration, memory, or a prior summary, follow
     the evidence and report the conflict.

## One-Prompt Version

Apply the authority constitution, then route before reading. For skills, use
`MASTER-INDEX.md` category/tag matching, then load one selected skill; use
semantic fallback only if the index is ambiguous (direct `local-rag` only with
collection selection; otherwise `rag-orchestrator` or the direct Chroma check).
For documents, use Obsidian for exact notes and `rag-orchestrator` for semantic
search, reading only cited chunks or the smallest cited file section. For
memories use Memoryweaver; for relationship questions use GraphRAG or Graphify.
Use the methodology charter for reasoning/build process guidance after routing.
Evidence beats narration and memory. Never crawl the vault or skill tree when
an index or backend can route the task.
