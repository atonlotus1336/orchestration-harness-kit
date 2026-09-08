# Orchestration Layer â€” Complete Setup & Troubleshooting Guide

> Reference for managing **RAG indexing**, **MCP systems**, and **memory synchronization** across Claude Code, Codex, and Hermes.

> Current Hermes Workspace launch baseline verified 2026-06-25: use `D:\MyVault\START-HERMES.cmd`. A healthy launch has Gateway `8642`, Dashboard `9119`, Workspace/Fusion `3000`, Ollama `11434`, and the Hermes OpenAI proxy `8105` ready. For first-response triage use `D:\MyVault\.claude\LLM-TRIAGE-CARD.md`; for literal recovery steps use `D:\MyVault\.claude\LLM-OPS-RUNBOOK.md`. Live output wins over this file.

---

## START HERE — Cold-Start Navigation Index

> Canonical nav chain (hermes-master-plan M12, 2026-07-07; purpose-first ordering added
> mission-layer Phase 8, 2026-07-08). A fresh session with zero context follows this in
> order; each doc routes deeper. Live output beats every doc.

**What is this FOR? (read this first — mission layer)** Before the ops chain: the
`D:\MyVault\.planning\missions\` portfolio holds the five domain missions this whole
system serves — **your domain missions (e.g. product, education, community)** (the last is CARE-governed: community data never leaves local
storage). Each `<slug>\MISSION.md` states the mission, metrics, and active initiatives;
`_digests\` holds the weekly/monthly/quarterly reviews. Purpose leads; the operational
chain below keeps that purpose alive.

| step | doc | when to use it |
|---|---|---|
| 1 | `D:\MyVault\.claude\LLM-TRIAGE-CARD.md` | first response — classify the symptom in 60 seconds |
| 2 | `D:\MyVault\.claude\LLM-OPS-RUNBOOK.md` | literal recovery / setup procedures |
| 3 | `D:\MyVault\.mcp-servers\CONNECTOR.md` | connect or configure a tool, script, or backend (shareable export: `export\orchestration-layer\docs\CONNECTOR.md`) |
| 4 | `D:\MyVault\.claude\HERMES-PARITY-TROUBLESHOOTING.md` | Hermes vs Claude/Codex parity or quality failures |
| 5 | `D:\MyVault\.planning\hermes-master-plan\PLAN.md` | phase gates, closure rules, invariants, future work |

Standing checks a cold session should know:
- **System health:** `powershell -NoProfile -ExecutionPolicy Bypass -File D:\MyVault\scripts\graph-gate.ps1` — statuses GREEN / RED / QUARANTINED / ACKED (ack = acknowledged external downtime, expiring).
- **Phase closure authority:** `PHASE-LEDGER.jsonl`, read ONLY through `D:\MyVault\tests\phase-green-resolver.ps1 -Phase Mxx`. Prose never closes a phase, and no phase closes before its dependencies resolve GREEN (PLAN.md §7 dependency graph + §9 standing rules).
- **Provider outages:** `D:\MyVault\tests\provider-health.ps1` — `BLOCKED_PROVIDER` (exit 3) means infrastructure, never a quality RED.

---

## Level 5 Readiness Gate

Last updated: 2026-07-02.

Before creating or executing the next Level 5 plan, run:

```powershell
# [PowerShell]
powershell -NoProfile -ExecutionPolicy Bypass -File D:\MyVault\.planning\level5-readiness-fix\level5-readiness-check.ps1
```

The default Level 5 MCP surface is intentionally small: `local-rag`,
`rag-orchestrator`, and `MyVault-control-plane`. GraphRAG, Obsidian,
autoresearch, and Comfy video orchestration are opt-in deep/domain profiles.

The readiness source of truth is:

- `D:\MyVault\.planning\level5-readiness-fix\READINESS-MATRIX.md`
- `D:\MyVault\.planning\level5-readiness-fix\RUNTIME-EXCEPTION-LEDGER.md`
- `D:\MyVault\.planning\level5-readiness-fix\LEVEL5-RUNTIME-POLICY.md`
- `D:\MyVault\.planning\level5-readiness-fix\READINESS-REPORT.md`

Do not treat the old C-drive Documents-based MyVault folder as an active project root.
Unavoidable C and WSL homes are runtime exceptions only, not MyVault project
storage.

## Current Optimization Map

Last verified: 2026-06-25 for Hermes Workspace launch health; deeper RAG/GraphRAG maturity notes retain their own dated evidence below.

The optimized path is **route first, read second**:

1. Apply `D:\MyVault\.claude\AUTHORITY-CONSTITUTION.md` when instructions,
   memories, project law, and evidence conflict.
2. Use `D:\MyVault\.claude\MASTER-INDEX.md` for skill/agent routing before
   opening any `SKILL.md`.
3. Use `D:\MyVault\.claude\RETRIEVAL-PLAYBOOK.md` to choose the cheapest source:
   Obsidian exact read, local-rag/skills index, Memoryweaver,
   GraphRAG, Graphify, or direct file read.
4. Use `D:\MyVault\.claude\contracts\` and
   `D:\MyVault\.claude\retrieval-evals\` only as subordinate contract/eval
   support. They enforce routing and freshness posture; they do not create a
   new authority layer.
5. Use `D:\MyVault\.claude\METHODOLOGY-CHARTER.md` for reasoning, build,
   answer-structure, and edge-case process guidance after routing.
6. Use `rag-orchestrator` as the preferred app-facing semantic interface.
   Direct `local-rag` and `graphrag` MCPs are useful for Claude
   Code/Codex diagnostics and specialized queries, but future apps should start
   with `rag-orchestrator` plus `obsidian`.

### Where token savings come from

Live counts and health change constantly â€” do not trust numbers written in
docs. Check live state with `gateway_status`/`rag_status` (rag-orchestrator
MCP), the local-rag `status` tool, or
`D:\MyVault\.mcp-servers\audit-orchestration.ps1`. The unified query endpoint is
`gateway_query`; `rag_query` and `rag_backend_query` remain compatibility
aliases. If fallback is used, the gateway reports `status: degraded` and
surfaces recovery/remediation metadata instead of flattening the failure into a
false healthy result.

| Layer | Efficiency | How it reduces reading |
|-------|------------|------------------------|
| Authority constitution | Conflict pruning | Prevents stale memory or narration from causing repeated backtracking. |
| Methodology charter | Process pruning | Gives agents a shared orient/constrain/execute/verify loop so they do not reload context to decide how to work. |
| MASTER-INDEX.md | Skill routing | Lets agents choose from hundreds of skills/agents by category/tag before loading one full skill (counts in the index header). |
| Adopted contracts + retrieval evals | Drift prevention | Keeps surface, registry, promotion, freshness, and Infinite Brain incorporation rules active through static evals and audit checks without adding upstream bloat. |
| `skills_index` | Semantic skill fallback | Chroma collection at `D:\MyVault\.rag\db`. Query via `D:\MyVault\.claude\scripts\query-skills-rag.py` when no client exposes collection-aware search. |
| Obsidian MCP | Exact note access | Reads known notes from `D:\MyVault` without scanning folders. |
| `rg` / direct file reads | Fast exact fallback | Best when Obsidian MCP search is slow or a path is already known. |
| local-rag | Hybrid keyword/vector corpus | General vault document corpus (NOT the skill index unless the client exposes collection selection). |
| Memoryweaver | Session/project memory | Best for prior decisions and recurring patterns. Always-on, file-based. |
| GraphRAG | Relationship/community queries | Query existing parquet outputs under `D:\MyVault\.rag\graphrag\output`; never auto-reindex. |
| Graphify | Code/doc graph traversal | Use existing `D:\MyVault\graphify-out\graph.json` for local relationship exploration when present. |

### Canonical future setup

For a new machine or rebuilt runtime, preserve this shape:

1. Put the vault, project MCP source, RAG stores, memory stores, and setup docs
   under `D:\MyVault`.
2. Keep only runtime glue in user-level homes:
   - `C:\Users\alice\.claude` for Claude Code startup/settings.
   - `C:\Users\alice\.codex` for Codex callable skills and global config.
   - `C:\Users\alice\.hermes` plus WSL `~/.hermes` for Hermes runtime
     profile cards.
   Skills three-root model: `D:\MyVault\.claude\skills` is the canonical shared
   root (junctioned from `~/.claude/skills`); `C:\Users\alice\.codex\skills`
   is the Codex runtime root (a real directory â€” Codex-only skills are edited
   there); `D:\MyVault\.codex\skills` is the backup mirror of the Codex runtime
   root. Mirror after Codex skill changes:
   `[PowerShell] powershell -File D:\MyVault\.claude\scripts\sync-codex-skills.ps1`.
   A new-machine restore needs `D:\MyVault` only â€” Codex skills restore from the
   mirror, not from regeneration.
3. Point every Obsidian MCP at `D:\MyVault`.
4. Preserve the shared routing docs:
   - `D:\MyVault\.claude\AUTHORITY-CONSTITUTION.md`
   - `D:\MyVault\.claude\RETRIEVAL-PLAYBOOK.md`
   - `D:\MyVault\.claude\METHODOLOGY-CHARTER.md`
   - `D:\MyVault\.claude\MASTER-INDEX.md`
5. Point every local-rag DB/cache at:
   - `DB_PATH=D:\MyVault\.rag\db`
   - `CACHE_DIR=D:\MyVault\.rag\cache`
6. Point every app at `rag-orchestrator` for merged semantic search, then add
   direct `local-rag` or `graphrag` only when the app benefits from
   direct diagnostics or specialized tools.
7. Rebuild the skill routing artifacts after changing skills or agents:
   ```powershell
   python D:\MyVault\.claude\scripts\build-skills-manifest.py
   python D:\MyVault\.claude\scripts\index-skills-rag.py
   ```
8. Run the read-only audit after setup:
   ```powershell
   powershell -ExecutionPolicy Bypass -File D:\MyVault\.mcp-servers\audit-orchestration.ps1
   ```
   The audit now prints unified gateway truth-state, backend recovery state, and
   any explicit remediation commands.
9. Run retrieval evals when changing authority, routing, MCP/RAG wiring, or
   adopted contracts:
   ```powershell
   python D:\MyVault\.claude\scripts\run-retrieval-evals.py --static
   ```
10. For WSL Hermes runtime profile guidance, use the opt-in SOUL sync only when
    the WSL profile tree is healthy:
    ```bash
    # [WSL/bash]
    MyVault_SYNC_WSL_HERMES_SOUL=1 \
      /home/alice/.hermes/hermes-agent/venv/bin/python \
      /mnt/d/MyVault/.mcp-servers/orchestration-contracts/sync_hermes_soul_guidance.py
    ```
    If WSL profile traversal is not healthy, wire the managed block manually
    from the WSL fallback section in
    `D:\MyVault\.mcp-servers\orchestration-contracts\README.md` into the active
    `~/.hermes/profiles/<profile>/SOUL.md`.
11. For process, routing, and token-efficiency drift reviews, use:
    `D:\MyVault\.claude\ORCHESTRATION-AUDIT-PROMPT.md`.
12. For step-by-step setup/recovery procedures executable by any model
    (Sonnet-class, GPT, open-source): `D:\MyVault\.claude\LLM-OPS-RUNBOOK.md`.

### Current known optimization risks

- Do not use full vault walks as a normal retrieval strategy. Generated trees
  under `.rag` are large; one complete `.rag` inventory currently spans about
  93k files and 2.6 GB.
- Some MCP clients expose local-rag without collection selection. In those
  clients, `skills_index` should be checked through direct Chroma or routed by
  `MASTER-INDEX.md`/`rag-orchestrator` instead of assuming
  `mcp__local-rag(collection="skills_index")` exists.
- GraphRAG indexing is expensive; do not auto-run it at startup. Query existing
  outputs and rebuild only on explicit maintenance.
- Hermes sessions can miss tools if a session is created before MCP registration
  completes. Use `/reload-mcp`, restart the session, or use the direct Python
  connector fallback.
- Adopted contracts are support material. They should carry
  `authority_rank: subordinate_contract` metadata and must not be retrieved as
  primary authority for conflicts, routing, or Codex layout decisions.

---

## Hermes Workspace Launch Contract

Use one user-facing launcher:

```powershell
[PowerShell] D:\MyVault\START-HERMES.cmd
```

That calls `D:\MyVault\hermes-workspace.bat`, starts or verifies the backend
pieces, prints the local/LAN URLs, launches the community comparison UIs as
companions, then starts the Workspace UI on port `3000`.

Known-good launch state:

The source-system screenshot is intentionally omitted from the portable kit.
Use the live health checks below as evidence.

The screenshot above is the visual baseline future agents should compare
against. It means:

| Card | Endpoint | Expected |
|---|---|---|
| Hermes Gateway | `http://127.0.0.1:8642/health` | `Ready` |
| Workspace Dashboard | `http://127.0.0.1:9119/api/status` | `Ready` |
| Ollama | `http://127.0.0.1:11434/api/tags` | `Ready` |
| Hermes OpenAI Proxy | `http://127.0.0.1:8105/v1/models` | `Ready` |
| MCP | `http://127.0.0.1:3000/api/connection-status` capability probe | `Available` |
| RAG | Fusion context probe combining MCP, memory, and dashboard state | `Available` |
| MEMORY | Workspace connection capability probe | `Available` |
| ORCHESTRATION | Dashboard/conductor/kanban capability probe | `Available` |

Fresh verification command:

```powershell
[PowerShell] @(
  'http://127.0.0.1:8642/health',
  'http://127.0.0.1:9119/api/status',
  'http://127.0.0.1:11434/api/tags',
  'http://127.0.0.1:8105/v1/models',
  'http://127.0.0.1:3000/api/connection-status',
  'http://127.0.0.1:3000/api/fusion/status'
) | ForEach-Object {
  try { Invoke-RestMethod -Uri $_ -TimeoutSec 5 | Out-Null; "$_ READY" }
  catch { "$_ DOWN: $($_.Exception.Message)" }
}
```

Known-good API interpretation from the 2026-06-25 verification:

```text
/api/connection-status:
  status = enhanced
  activeModel = gemma4-ollamauncensored-32k:latest
  capabilities.mcp = true
  capabilities.mcpNative = false
  capabilities.mcpFallback = true
  capabilities.memory = true
  orchestration.mcp = config-fallback
  orchestration.globalServicesReady = true

/api/fusion/status:
  agenticReady = true
  advisoryReady = true
  contextSystems.mcp = true
  contextSystems.rag = true
  contextSystems.memory = true
  contextSystems.orchestration = true
```

Fusion lane/profile semantics:

- The Fusion `Prompt Sent To Selected Models` destination list is the set of
  actual Council lanes that receive the prompt.
- The `Hermes Runtime Profile` selector is not an extra LLM lane. It is the
  profile context used to create and dispatch those lanes through Hermes.
- The runtime profile carries auth/provider wiring, tool and approval context,
  MCP/RAG/Memory/Orchestration wiring, Telegram/session behavior, and the
  profile default model as a fallback.
- Selected Fusion models are passed as per-lane `modelOverride` values. When a
  selected lane has a model override, that selected model wins over the runtime
  profile's default model.
- Agentic file access depends on agentic mode, workspace-root validation,
  bridge/tool support, and approvals. Selecting a runtime profile alone does not
  guarantee file writes.
- Agentic Workspace Root can be selected from saved roots or added with the
  Fusion screen's `Add Agentic Folder` control. That control posts the chosen
  path to `/api/workspace`; the backend requires an existing directory, rejects
  Hermes profile/state directories and system paths, saves the root in
  profile-local `webui_state`, and then Fusion accepts it as an allowed root.

Implementation references:

- `D:\MyVault\hermes-webui\hermes-workspace\src\routes\api\fusion\runs.ts`
  builds lanes with `profileName: model.profileName ?? plan.profileName` and
  `modelOverride: model.modelOverride`.
- `D:\MyVault\hermes-webui\hermes-workspace\src\routes\api\workspace.ts`
  owns custom workspace-root persistence and validation.
- `D:\MyVault\hermes-webui\hermes-workspace\src\server\council\controller.ts`
  creates and sends each lane session with `profileName` and `modelOverride` as
  separate fields.

`capabilities.mcpNative=false` with `capabilities.mcpFallback=true` is not a
red failure on this install. It means native dashboard MCP runtime endpoints are
not exposed, but Workspace can still read configured MCP/RAG/Memory wiring
through the dashboard/config fallback. Treat that as usable for health display,
model selection, and handoff. Do not claim the native MCP runtime is fully
healthy until native probes are responsive.

If the Fusion cards are degraded, troubleshoot in this order:

1. Verify gateway `8642` and dashboard `9119`.
2. Verify Workspace `/api/connection-status`.
3. Confirm the active profile still contains the global `providers`,
   `mcp_servers`, `memory`, and `toolsets` blocks.
4. Run `D:\MyVault\scripts\reconcile-hermes-orchestration.ps1`.
5. Only after those pass should you inspect React display logic.

Fusion Agentic note: `agenticReady=true` means Fusion can dispatch agentic
Council lanes through the Workspace stack. Do not claim file writes are
guaranteed until the lane bridge has passed a file-write smoke test inside the
validated workspace root. File-changing work must stay in Hermes
Workspace/Fusion or the regular Hermes agentic flow, not a community UI.

Fusion orchestration upgrade roadmap:

- Level 3 role prompts (implemented 2026-06-25):
  `D:\MyVault\hermes-webui\hermes-workspace\docs\plans\2026-06-25-hermes-fusion-level-3-role-prompts-gsd-plan.md`
- Level 4 phased orchestration:
  `D:\MyVault\hermes-webui\hermes-workspace\docs\plans\2026-06-25-hermes-fusion-level-4-phased-orchestration-gsd-plan.md`
- Level 5 rules orchestrator:
  `D:\MyVault\hermes-webui\hermes-workspace\docs\plans\2026-06-25-hermes-fusion-level-5-rules-orchestrator-gsd-plan.md`

These plans are model-agnostic by design. Role and phase assignment must be
derived from the user's prompt, task risk, workspace context, and the selected
model pool at run time. Do not encode fixed model-role rules such as a specific
vendor model always being planner, writer, reviewer, or judge.

---

## Architecture Overview

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                    Unified RAG/MCP/Memory Backbone              â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚                                                                  â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚
â”‚  â”‚ Memoryweaver â”‚   â”‚  GraphRAG    â”‚   â”‚   Local-RAG      â”‚   â”‚
â”‚  â”‚ (file-based) â”‚   â”‚  (Graphed    â”‚   â”‚  (ChromaDB       â”‚   â”‚
â”‚  â”‚   memory)    â”‚   â”‚   indexing)  â”‚   â”‚   local)         â”‚   â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚
â”‚                                                                  â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚
â”‚  â”‚  Obsidian    â”‚   â”‚ MemoryWeaver â”‚   â”‚  AutoResearch    â”‚   â”‚
â”‚  â”‚  Vault MCP   â”‚   â”‚  (profiles)  â”‚   â”‚  (research)      â”‚   â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚
â”‚                                                                  â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚         RAG-Orchestrator (integration point)             â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â”‚                                                                  â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
         â†‘                    â†‘                       â†‘
         â”‚                    â”‚                       â”‚
    â”Œâ”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”      â”Œâ”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”      â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”
    â”‚            â”‚      â”‚            â”‚      â”‚              â”‚
â”Œâ”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â” â”Œâ”€â”€â–¼â”€â”€â” â”Œâ”€â–¼â”€â”€â”€â”€â”€â”€â” â”Œâ”€â”€â–¼â”€â”€â” â”Œâ”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â” â”Œâ”€â”€â–¼â”€â”€â”
â”‚ Claude   â”‚ â”‚CLI  â”‚ â”‚Hermes  â”‚ â”‚WUI  â”‚ â”‚ Codex   â”‚ â”‚CLI  â”‚
â”‚  Code    â”‚ â”‚     â”‚ â”‚ CLI    â”‚ â”‚/WS  â”‚ â”‚(IDE)    â”‚ â”‚     â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ â””â”€â”€â”€â”€â”€â”˜ â””â”€â”€â”€â”€â”€â”€â”€â”€â”˜ â””â”€â”€â”€â”€â”€â”˜ â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ â””â”€â”€â”€â”€â”€â”˜
```

**Shared spine:**
- All three systems (Claude Code, Hermes, Codex) pull from the **same RAG/MCP/memory backbone**
- Updates in one place affect all three systems
- Profile/model switching in one doesn't shadow global orchestration layer

---

## Quick Health Check

**Run this when troubleshooting failed fetches, degraded Fusion cards, or MCP
unavailability:**

```powershell
[PowerShell] @(
  'http://127.0.0.1:8642/health',
  'http://127.0.0.1:9119/api/status',
  'http://127.0.0.1:11434/api/tags',
  'http://127.0.0.1:8105/v1/models',
  'http://127.0.0.1:3000/api/connection-status',
  'http://127.0.0.1:3000/api/fusion/status'
) | ForEach-Object {
  try { Invoke-RestMethod -Uri $_ -TimeoutSec 5 | Out-Null; "$_ READY" }
  catch { "$_ DOWN: $($_.Exception.Message)" }
}
```

---

## Component Health Checks

### Retired document-management integration

The former document-management backend was removed because its file-handling
surface was not appropriate for a source vault. Do not restore an automated
backend that can delete or mutate files in its input directory. Use Obsidian
for exact reads, local-rag for semantic document search, Memoryweaver for past
decisions, and GraphRAG for relationships.

---

### Hermes Gateway (WebUI/Workspace Connection)

**Status check:** current Workspace launcher uses gateway `8642`.
```powershell
[PowerShell] Invoke-RestMethod http://127.0.0.1:8642/health
```

**If failing:**
```bash
# 1. Is it running?
pgrep -af "hermes.*gateway" || echo "NOT RUNNING"

# 2. Is API server enabled in .env?
grep "API_SERVER_ENABLED" ~/.hermes/.env || echo "NOT CONFIGURED"

# 3. Start gateway (the active profile is telegram, not a default; --replace re-binds if one is already running)
hermes --profile telegram gateway run --replace
```

**Common issues:**
| Issue | Fix |
|-------|-----|
| "API_SERVER_ENABLED not found" | Add to `~/.hermes/.env`: `API_SERVER_ENABLED=true` |
| Port 8642 already in use | Kill process: `lsof -i :8642 \| grep -v COMMAND \| awk '{print $2}' \| xargs kill` |
| Auth failed | Run: `bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/ensure-hermes-startup-auth.sh` |

---

### MCP Servers

**Claude Code MCPs:**
```bash
# All should be running (use lsof on macOS/Linux, ps on Windows)
ps aux | grep -E "node.*mcp-|python.*mcp-|graphrag" | grep -v grep
```

Expected:
- `mcp-obsidian` (Node.js)
- `mcp-local-rag` (Node.js)
- `graphrag-wrapper-wsl.sh` (Bash wrapper)
- `autoresearch-wrapper-wsl.sh` (Bash wrapper)
- `rag-orchestrator` (Python, only if using Hermes)

**Hermes MCPs** (in gateway process):
- Find the live profile config, then grep the raw YAML (the styled summary from `hermes config` does NOT grep): `hermes config path` then `grep mcp_servers "$(hermes config path)"`
- Verify paths exist: `ls -la /mnt/c/Users/alice/.claude/mcp-wrappers/`

**If MCP fails to load in Claude Code:**
1. Check MCP is installed: `npm list -g mcp-obsidian mcp-local-rag`
2. Check Node path in settings.json matches your `node --version`
3. Restart Claude Code

---

### Memory Systems

**MemoryWeaver ingestion** (Claude Code):
```bash
# Check if memory is being written
ls -lah /mnt/d/MyVault/.claude/Memory/memoryweaver/ | head -10

# Recent ingestions
stat /mnt/d/MyVault/.claude/Memory/memoryweaver/latest 2>/dev/null || echo "No recent ingestions"
```

**Hermes memory** (user profile + session memory):
```bash
# Check if Hermes config has memory enabled (grep the raw YAML, not the styled `hermes config` summary)
grep -A5 "memory:" "$(hermes config path)"

# Expected: memory_enabled: true, user_profile_enabled: true
```

---

## Setup & Sync Procedures

### Initial Setup (From Scratch)

**1. Ensure all services are installed:**
```bash
# Ollama
curl http://10.0.0.1:11434/api/tags 2>/dev/null || echo "Ollama not running"

# Node MCPs
npm list -g mcp-obsidian mcp-local-rag

# Python venvs for Hermes MCPs
# rag-orchestrator: canonical env is .venv (venv is a compatibility symlink to it); always call bin/python3.11 explicitly
ls /home/alice/.local/share/rag-orchestrator/.venv 2>/dev/null || echo "Missing rag-orchestrator"
ls /home/alice/.local/share/memoryweaver-mcp/venv 2>/dev/null || echo "Missing memoryweaver-mcp"
```

**2. If services missing, install:**
```bash
# Ollama â€” already running
# (verify with curl http://10.0.0.1:11434/api/tags)

# Node MCPs
npm install -g mcp-obsidian mcp-local-rag

# Python venvs for Hermes â€” rag-orchestrator runs from LOCAL SOURCE (D:\MyVault\.mcp-servers\rag-orchestrator\server.py),
# it is NOT a PyPI package. The canonical env is .venv (uv-managed); see CONNECTOR.md lines 122-125 and LLM-OPS-RUNBOOK.md Section 7.
mkdir -p /home/alice/.local/share/rag-orchestrator
uv venv /home/alice/.local/share/rag-orchestrator/.venv --python 3.11
uv pip install --python /home/alice/.local/share/rag-orchestrator/.venv/bin/python3.11 chromadb  # plus whatever server.py imports
ln -s .venv /home/alice/.local/share/rag-orchestrator/venv  # recreate the compatibility symlink

# memoryweaver-mcp (if custom, ensure source exists)
# D:\MyVault\.mcp-servers\memoryweaver-mcp\server.py
```

**3. Sync profiles across systems:**
```bash
# For Hermes
cd /mnt/d/MyVault/hermes-agent-self-evolution/scripts
bash sync-g-managed-telegram-profile.sh
bash apply-hermes-profile-to-wsl.sh
bash ensure-hermes-startup-auth.sh
bash ensure-rag-stack.sh
bash ensure-orchestration-layer.sh

# For Claude Code & Codex
# (use their respective sync procedures or restart them)
```

---

### Cross-System Sync

**When updating RAG/MCP configuration:**

1. **Update shared sources first:**
   - Obsidian vault â†’ documented
   - local-rag index â†’ already centralized
   - Skills folder â†’ `/mnt/d/MyVault/.claude/skills`

2. **Sync to each system:**
   ```bash
   # Hermes
   bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh
   
   # Claude Code
   # (restart Claude Code to reload MCP config from settings.json)
   
   # Codex
   # (configuration-specific â€” check its MCP config location)
   ```

3. **Verify each system sees changes:**
   ```bash
   # Hermes â€” do NOT curl :8642/mcp/list (often NOT the live gateway port).
   # Discover the live port first, or check the MCP list in the Hermes UI.
   # Correct health checks: LLM-OPS-RUNBOOK Â§3 / LLM-TRIAGE-CARD.
   
   # Claude Code
   # (Check MCP tab in UI or terminal logs)
   ```

---

## Troubleshooting: Common Failure Modes

> For literal, any-model step-by-step recovery, follow `D:\MyVault\.claude\LLM-OPS-RUNBOOK.md` (Section 3 health check, Section 4 failure procedures) and the read-only audit `D:\MyVault\.mcp-servers\audit-orchestration.ps1`. Canonical MCP wiring + the .venv/symlink details live in `D:\MyVault\.mcp-servers\CONNECTOR.md` (2026-06-12). The command examples in the subsections below were last revised 2026-06-09 â€” when they disagree with the runbook, CONNECTOR.md, or live output, the runbook and live output win.

### "Fails to Fetch" in Hermes WebUI/Workspace

**Symptom:** Messages don't send, get network errors.

**Diagnosis:**
```bash
# 1. Is gateway running?
curl -sf http://127.0.0.1:8642/health || echo "Gateway DOWN"

# 1b. Is the dashboard running?
curl -sf http://127.0.0.1:9119/api/status || echo "Dashboard DOWN"

# 2. Are MCP servers loaded? (grep the raw YAML; the styled `hermes config` summary cannot be grepped)
grep -A20 "mcp_servers:" "$(hermes config path)" | head -25

# 3. Check gateway logs (active profile is telegram; --replace re-binds an existing gateway)
hermes --profile telegram gateway run --replace 2>&1 | head -50
```

**Fix:**
1. âœ“ Ensure `API_SERVER_ENABLED=true` in `~/.hermes/.env`
2. âœ“ Start gateway (active profile is telegram): `hermes --profile telegram gateway run --replace`
3. âœ“ Sync profile: `bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/ensure-orchestration-layer.sh`
4. âœ“ Restart WebUI/Workspace

---

### MCP Server Won't Load (Claude Code)

**Symptom:** Tool calls fail, "MCP not available" errors.

**Diagnosis:**
```bash
# Check if MCP process is running
ps aux | grep mcp-obsidian

# Check if path in the active config exists (canonical npm global root is D:\MyVault\tools\npm\global per C:\Users\alice\.npmrc)
ls /mnt/d/MyVault/tools/npm/global/node_modules/mcp-obsidian

# Try running MCP directly to see errors
node /mnt/d/MyVault/tools/npm/global/node_modules/mcp-obsidian/dist/index.js /mnt/d/MyVault
```

**Fix:**
1. âœ“ Reinstall MCP: `npm install -g mcp-obsidian` (lands in D:\MyVault\tools\npm\global per C:\Users\alice\.npmrc prefix)
2. âœ“ The active config is `D:\MyVault\.mcp.json` and it already points at `D:\MyVault\tools\npm\global\node_modules\mcp-obsidian\dist\index.js` â€” do NOT edit a stale AppData path; verify .mcp.json matches the canonical D:\MyVault\tools root
3. âœ“ Restart Claude Code

---

### Memory Not Syncing

**Symptom:** MemoryWeaver isn't ingesting session data.

**Diagnosis:**
```bash
# Check if memory ingest hook runs
ls -la /mnt/d/MyVault/.claude/Memory/memoryweaver/ | tail -5

# Check Claude Code hook is configured
grep -A5 "memoryweaver-spool" ~/.claude/settings.json

# Run manual ingest
python3 /mnt/c/Users/alice/.claude/hooks/memoryweaver-spool.py
```

**Fix:**
1. âœ“ Verify hook in `settings.json` under `"hooks": {"Stop": ...}`
2. âœ“ Run ingest manually: `python3 memoryweaver-spool.py`
3. âœ“ Check directory exists: `mkdir -p /mnt/d/MyVault/.claude/Memory/memoryweaver`

---

### RAG Index Not Updating

**Symptom:** Obsidian notes don't appear in search results, local-rag returns stale data.

**Diagnosis:**
```bash
# Local-RAG last update
ls -la /mnt/d/MyVault/.rag/db/ | tail -5

# Check if Obsidian vault is monitored
grep -i obsidian ~/.claude/settings.json
```

**Fix:**
1. Do not run an automated document-management backend against the vault root.
2. For local-rag, re-ingest via its MCP tools (e.g. `mcp__local-rag__ingest_file` / `ingest_data`) against `BASE_DIR=D:\MyVault`. There is no `local-rag-ingest` CLI binary; local-rag is an MCP server (`mcp-local-rag`). DB at `D:\MyVault\.rag\db`, cache at `D:\MyVault\.rag\cache`.

---

## Configuration Files Reference

| System | Config File | MCP Servers |
|--------|-------------|------------|
| **Claude Code project** | `D:\MyVault\.mcp.json` | obsidian, local-rag, graphrag, rag-orchestrator, autoresearch, comfy-video-orchestrator |
| **Claude Code user** | `C:\Users\alice\.claude\settings.json` | startup hooks, permissions, user-level MCP allowances; project `.mcp.json` supplies MyVault MCPs when launched from `D:\MyVault` |
| **Codex user** | `C:\Users\alice\.codex\config.toml` | obsidian, local-rag, graphrag, rag-orchestrator, autoresearch, comfy-video-orchestrator, wordpress |
| **Codex project** | `D:\MyVault\.codex\config.toml` | obsidian, local-rag, graphrag, rag-orchestrator, autoresearch |
| **Hermes WSL root** | `~/.hermes/config.yaml` | obsidian, rag-orchestrator, memoryweaver-mcp, wordpress |
| **Hermes WSL profile** | `~/.hermes/profiles/<profile>/config.yaml` | synced from root; active sessions must reload MCP after changes |
| **Hermes Windows cards** | `C:\Users\alice\.hermes\profiles\<profile>\config.yaml` | profile cards mirrored into WSL runtime as needed |

---

> **context-mode** (added 2026-06-14) is wired per-harness alongside the above: Claude Code via marketplace plugin, Codex via `[mcp_servers.context-mode]` in `config.toml`, Hermes via a `context-mode:` entry in the profile + root `config.yaml`. It is a context-window optimizer (11 `ctx_*` tools), NOT a RAG backend â€” it sits beside the RAG stack. Full per-harness matrix, store locations, and the Windows-vs-WSL sandbox split: `D:\MyVault\.mcp-servers\CONNECTOR.md` (context-mode section).

## Backbone State & Maturity (verified 2026-06-15)

Snapshot of which tiers are live. Re-check with `/layer-status` or `memoryweaver status`.

- **GraphRAG** (`.rag/graphrag`, wired to the `graphrag` MCP): **populated** â€” index complete
  (entities / relationships / communities / community_reports present).
- **local-rag** (`.rag/db`): document RAG corpus live.
- **MemoryWeaver**: **healthy & consistent**. `memoryweaver status` reports Chroma
  explicit=6 + inferred=48 = **54 == fts_index 54**. The Chroma vector tier is populated and
  embedded **locally** â€” ChromaDB's default ONNX MiniLM via `documents=` upsert, **no
  pay-per-token API** â€” persisted at `Memory/memoryweaver/chroma/`; the FTS5/BM25 mirror lives
  on the ext4 runtime path. Hybrid recall (vector RRF + BM25) is active. NOTE: a **stale empty**
  `Memory/memoryweaver/chroma.sqlite3` sits *beside* the real `chroma/` directory â€” ignore it;
  the adapter persists to the subdir (`MW_DATA_DIR/chroma`). Authoritative counts come from
  `memoryweaver status`, not a raw read of that top-level file.
- **mempalace** (Codex `mempalace` MCP): a separate experimental graph subsystem, outside the
  RAG/memory hot path; not audited here.

## Maintenance Checklist

**Weekly:**
- [ ] Verify Hermes Workspace launch health: `[PowerShell] Invoke-RestMethod http://127.0.0.1:3000/api/fusion/status`
- [ ] Verify gateway and dashboard: `[PowerShell] Invoke-RestMethod http://127.0.0.1:8642/health; Invoke-RestMethod http://127.0.0.1:9119/api/status`
- [ ] Check memory ingestion: `ls -la /mnt/d/MyVault/.claude/Memory/memoryweaver/`

**Monthly:**
- [ ] Confirm no automated document-management backend targets the vault root.
- [ ] Sync Hermes profile to WSL: `bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh`
- [ ] Update MCP packages: `npm update -g mcp-obsidian mcp-local-rag`
- [ ] Check disk space in `D:\MyVault\.rag` (should be < 80% full)

**Before major updates:**
1. Backup memory: `cp -r /mnt/d/MyVault/.claude/Memory/memoryweaver ~/backup/`
2. Backup RAG: `cp -r /mnt/d/MyVault/.rag ~/backup/`
3. Document active sessions
4. Run full sync: `bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/ensure-orchestration-layer.sh`

**Surface graph gate:**
- The orchestration surface registry lives at `D:\MyVault\SURFACE-GRAPH.yaml`.
- Run `[PowerShell] powershell -NoProfile -ExecutionPolicy Bypass -File D:\MyVault\scripts\graph-gate.ps1 -Mode all` after changing MCP servers, profile configs, evaluation baselines, or orchestration docs.
- The daily `MyVault-Hermes-Orchestration-Audit` scheduled task runs `D:\MyVault\.mcp-servers\audit-orchestration.ps1 -RunGraphGate` and propagates graph-gate failures so Windows Task Scheduler can show non-green runs.
- Doc freshness is intentional: if this graph changes, update this architecture note so `doc-orchestration-layer` returns GREEN again.

**Lifecycle parity plugin:**
- M5 adds the standalone `MyVault-parity` plugin at `D:\MyVault\hermes-custom-plugins\MyVault-parity`.
- Managed `home` and `telegram` profiles enable it through `plugins.enabled`, and rendered profile plugin directories link back to the G: source.
- On first turn, the plugin injects a fresh MemoryWeaver digest or a `MEMORY DIGEST STALE` RED notice; session/kanban hooks append `MyVault-parity-ledger.jsonl` rows keyed by `session_id`.
- M5 profile-presence graph probes are quiet on missing files so dry-run mutation reports stay readable while still returning RED.
- The Codex M5 review pass tightened refresh idempotency: failed MemoryWeaver refreshes no longer leave a permanent session marker, so a later session-finalize path can retry.

---

## Key Ports & Services

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| Hermes Workspace UI / Fusion | 127.0.0.1 / LAN host | 3000 | Main user interface and `/fusion` |
| Hermes Gateway | 127.0.0.1 | 8642 | OpenAI-compatible Hermes Agent gateway |
| Hermes Dashboard | 127.0.0.1 | 9119 | Dashboard/status/session/config APIs |
| Hermes OpenAI Proxy | 127.0.0.1 | 8105 | Local browser-safe proxy to Hermes/Ollama model catalog |
| Ollama | 127.0.0.1 | 11434 | Local Ollama daemon and Ollama Cloud subscription catalog |
| Ollama | 10.0.0.1 | 11434 | WSL route to Windows Ollama used by Hermes-in-WSL |

> Historical note: older launchers and notes mention `8787`. The current
> canonical `START-HERMES.cmd` path verified on 2026-06-25 uses `8642`,
> `9119`, `3000`, `8105`, and `11434`. If live output differs, record the
> difference and trust the live process list.

---

## When to Escalate

File an issue if:
- Gateway fails to start after `ensure-orchestration-layer.sh`
- MCP wrapper scripts fail (not a simple path/permission issue)
- Memory ingestion never completes despite configuration being correct
- RAG rebuild hangs for >10 minutes

Before escalating, gather:
```bash
# Logs (active profile is telegram; --replace re-binds an existing gateway)
hermes --profile telegram gateway run --replace 2>&1 | tee /tmp/hermes-gateway.log
# Config snapshot (copy the raw active config; `hermes config` only prints a styled summary)
cp "$(hermes config path)" /tmp/hermes-config.yaml
cat ~/.hermes/.env > /tmp/hermes-env.txt

# File structure
ls -la /mnt/d/MyVault/.rag > /tmp/rag-structure.txt
ls -la /mnt/d/MyVault/.claude/Memory > /tmp/memory-structure.txt
```

---

**Last updated:** 2026-07-05  
**Maintained by:** MyVault Orchestration Layer Team


<!-- SURFACE-GRAPH sync 2026-07-05: graph-gate hardened - semantic probes (profiles/beacon/baseline + eval-fixtures hash-drift), secret-scan + verify_cmd passes, real break-RED-restore mutation test, discovery scope documented (red-table R1-R7). Canonical gate: D:\MyVault\scripts\graph-gate.ps1 ; registry: D:\MyVault\SURFACE-GRAPH.yaml. -->
