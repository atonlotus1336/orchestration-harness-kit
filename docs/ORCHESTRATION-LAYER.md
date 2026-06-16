# Orchestration Layer — Complete Setup & Troubleshooting Guide

> Reference for managing **RAG indexing**, **MCP systems**, and **memory synchronization** across Claude Code, Codex, and Hermes.

> WARNING (small models): this doc is the architecture overview and some troubleshooting commands are STALE (2026-06-09). For first-response triage use `D:\MyVault\.claude\LLM-TRIAGE-CARD.md`; for literal recovery steps use `D:\MyVault\.claude\LLM-OPS-RUNBOOK.md`. Live output wins over this file.

---

## Current Optimization Map

Last verified: 2026-06-12.

The optimized path is **route first, read second**:

1. Apply `D:\MyVault\.claude\AUTHORITY-CONSTITUTION.md` when instructions,
   memories, project law, and evidence conflict.
2. Use `D:\MyVault\.claude\MASTER-INDEX.md` for skill/agent routing before
   opening any `SKILL.md`.
3. Use `D:\MyVault\.claude\RETRIEVAL-PLAYBOOK.md` to choose the cheapest source:
   Obsidian exact read, local-rag/skills index, Memoryweaver, LightRAG,
   GraphRAG, Graphify, or direct file read.
4. Use `D:\MyVault\.claude\METHODOLOGY-CHARTER.md` for reasoning, build,
   answer-structure, and edge-case process guidance after routing.
5. Use `rag-orchestrator` as the preferred app-facing semantic interface.
   Direct `local-rag`, `lightrag`, and `graphrag` MCPs are useful for Claude
   Code/Codex diagnostics and specialized queries, but future apps should start
   with `rag-orchestrator` plus `obsidian`.

### Where token savings come from

Live counts and health change constantly — do not trust numbers written in
docs. Check live state with `rag_status` (rag-orchestrator MCP), the local-rag
`status` tool, or `D:\MyVault\.mcp-servers\audit-orchestration.ps1`.

| Layer | Efficiency | How it reduces reading |
|-------|------------|------------------------|
| Authority constitution | Conflict pruning | Prevents stale memory or narration from causing repeated backtracking. |
| Methodology charter | Process pruning | Gives agents a shared orient/constrain/execute/verify loop so they do not reload context to decide how to work. |
| MASTER-INDEX.md | Skill routing | Lets agents choose from hundreds of skills/agents by category/tag before loading one full skill (counts in the index header). |
| `skills_index` | Semantic skill fallback | Chroma collection at `D:\MyVault\.rag\db`. Query via `D:\MyVault\.claude\scripts\query-skills-rag.py` when no client exposes collection-aware search. |
| Obsidian MCP | Exact note access | Reads known notes from `D:\MyVault` without scanning folders. |
| `rg` / direct file reads | Fast exact fallback | Best when Obsidian MCP search is slow or a path is already known. |
| local-rag | Hybrid keyword/vector corpus | General vault document corpus (NOT the skill index unless the client exposes collection selection). |
| Memoryweaver | Session/project memory | Best for prior decisions and recurring patterns. Always-on, file-based. |
| LightRAG | Vault semantic/graph RAG | Windows service on port 9621 (`http://10.0.0.1:9621` from WSL), input directory `D:\MyVault`. |
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
   is the Codex runtime root (a real directory — Codex-only skills are edited
   there); `D:\MyVault\.codex\skills` is the backup mirror of the Codex runtime
   root. Mirror after Codex skill changes:
   `[PowerShell] powershell -File D:\MyVault\.claude\scripts\sync-codex-skills.ps1`.
   A new-machine restore needs `D:\MyVault` only — Codex skills restore from the
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
   direct `local-rag`, `lightrag`, or `graphrag` only when the app benefits from
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
9. For process, routing, and token-efficiency drift reviews, use:
   `D:\MyVault\.claude\ORCHESTRATION-AUDIT-PROMPT.md`.
10. For step-by-step setup/recovery procedures executable by any model
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

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Unified RAG/MCP/Memory Backbone              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │   LightRAG   │   │  GraphRAG    │   │   Local-RAG      │   │
│  │ (10.0.0.1│   │  (Graphed    │   │  (ChromaDB       │   │
│  │   :9621)     │   │   indexing)  │   │   local)         │   │
│  └──────────────┘   └──────────────┘   └──────────────────┘   │
│                                                                  │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │  Obsidian    │   │ MemoryWeaver │   │  AutoResearch    │   │
│  │  Vault MCP   │   │  (profiles)  │   │  (research)      │   │
│  └──────────────┘   └──────────────┘   └──────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │         RAG-Orchestrator (integration point)             │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
         ↑                    ↑                       ↑
         │                    │                       │
    ┌────┴──────┐      ┌─────┴──────┐      ┌────────┴─────┐
    │            │      │            │      │              │
┌───▼──────┐ ┌──▼──┐ ┌─▼──────┐ ┌──▼──┐ ┌─▼────────┐ ┌──▼──┐
│ Claude   │ │CLI  │ │Hermes  │ │WUI  │ │ Codex   │ │CLI  │
│  Code    │ │     │ │ CLI    │ │/WS  │ │(IDE)    │ │     │
└──────────┘ └─────┘ └────────┘ └─────┘ └─────────┘ └─────┘
```

**Shared spine:**
- All three systems (Claude Code, Hermes, Codex) pull from the **same RAG/MCP/memory backbone**
- Updates in one place affect all three systems
- Profile/model switching in one doesn't shadow global orchestration layer

---

## Quick Health Check

**Run this when troubleshooting fails to fetch errors or MCP unavailability:**

```bash
# 1. LightRAG running?
curl -s http://10.0.0.1:9621/health | jq '.status'

# 2. Hermes gateway running? (if using WebUI/Workspace) — port varies by launcher; discover first.
# Observed 8787 on 2026-06-14; 8642 was the older documented port. Discover the live port, then check it:
HPORT=$(ss -ltnp 2>/dev/null | grep -Eo '127\.0\.0\.1:(8787|8642)' | grep -Eo '[0-9]+$' | head -1)
curl -s "http://127.0.0.1:${HPORT:-8787}/health" | jq '.status'

# 3. MCP servers in Claude Code loaded?
ps aux | grep -E "mcp-obsidian|mcp-local-rag|graphrag" | grep -v grep

# 4. Memory system synced?
ls -la /mnt/d/MyVault/.claude/Memory/memoryweaver 2>/dev/null | head -5

# 5. Skills indexed?
ls -la /mnt/d/MyVault/.claude/skills/ 2>/dev/null | wc -l
```

---

## Component Health Checks

### LightRAG (Vector + Graph Indexing)

**Status check:**
```bash
curl -s http://10.0.0.1:9621/health | jq .
```

**Healthy =** `.status` is `"healthy"`. Do NOT exact-match the whole body — the live response also includes `configuration` (e.g. `llm_model: mistral:latest`, `embedding_model: nomic-embed-text`), `pipeline_busy`, `api_version`, etc., and these fields/values vary. Sample shape:
```json
{
  "status": "healthy",
  "webui_available": true,
  "working_directory": "G:\\MyVault\\.rag\\lightrag",
  "core_version": "1.4.15"
}
```

**If failing:**
- LightRAG service crashed — restart on Windows: `[PowerShell] D:\MyVault\.rag\start-lightrag.bat` (must bind `--host 0.0.0.0 --port 9621`, not 127.0.0.1, or WSL clients cannot reach it). Hermes startup also auto-starts it via `[WSL/bash] bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/ensure-rag-stack.sh` (a script path, never a `hermes` subcommand).
- Check disk space in `D:\MyVault\.rag\lightrag`
- Check Ollama (LightRAG backend) is running: `curl http://10.0.0.1:11434/api/tags`

---

### Hermes Gateway (WebUI/Workspace Connection)

**Status check:** (Hermes WebUI/gateway port varies by launcher — observed 8787 on 2026-06-14, 8642 in older configs; discover the live port first)
```bash
HPORT=$(ss -ltnp 2>/dev/null | grep -Eo '127\.0\.0\.1:(8787|8642)' | grep -Eo '[0-9]+$' | head -1)
curl -s "http://127.0.0.1:${HPORT:-8787}/health" && echo "OK" || echo "FAIL"
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
# LightRAG
curl http://10.0.0.1:9621/health 2>/dev/null || echo "LightRAG not running"

# Ollama (LightRAG backend)
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
# LightRAG — use your existing setup
# (already healthy at 10.0.0.1:9621)

# Ollama — already running
# (verify with curl http://10.0.0.1:11434/api/tags)

# Node MCPs
npm install -g mcp-obsidian mcp-local-rag

# Python venvs for Hermes — rag-orchestrator runs from LOCAL SOURCE (D:\MyVault\.mcp-servers\rag-orchestrator\server.py),
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
   - Obsidian vault → documented
   - RAG index (LightRAG) → already centralized
   - Skills folder → `/mnt/d/MyVault/.claude/skills`

2. **Sync to each system:**
   ```bash
   # Hermes
   bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh
   
   # Claude Code
   # (restart Claude Code to reload MCP config from settings.json)
   
   # Codex
   # (configuration-specific — check its MCP config location)
   ```

3. **Verify each system sees changes:**
   ```bash
   # Hermes — do NOT curl :8642/mcp/list (often NOT the live gateway port).
   # Discover the live port first, or check the MCP list in the Hermes UI.
   # Correct health checks: LLM-OPS-RUNBOOK §3 / LLM-TRIAGE-CARD.
   
   # Claude Code
   # (Check MCP tab in UI or terminal logs)
   ```

---

## Troubleshooting: Common Failure Modes

> For literal, any-model step-by-step recovery, follow `D:\MyVault\.claude\LLM-OPS-RUNBOOK.md` (Section 3 health check, Section 4 failure procedures) and the read-only audit `D:\MyVault\.mcp-servers\audit-orchestration.ps1`. Canonical MCP wiring + the .venv/symlink details live in `D:\MyVault\.mcp-servers\CONNECTOR.md` (2026-06-12). The command examples in the subsections below were last revised 2026-06-09 — when they disagree with the runbook, CONNECTOR.md, or live output, the runbook and live output win.

### "Fails to Fetch" in Hermes WebUI/Workspace

**Symptom:** Messages don't send, get network errors.

**Diagnosis:**
```bash
# 1. Is gateway running? (port varies — observed 8787 on 2026-06-14, 8642 in older configs; discover first)
HPORT=$(ss -ltnp 2>/dev/null | grep -Eo '127\.0\.0\.1:(8787|8642)' | grep -Eo '[0-9]+$' | head -1)
curl -sf "http://127.0.0.1:${HPORT:-8787}/health" || echo "Gateway DOWN (check the live port — it may be serving on 8787)"

# 2. Are MCP servers loaded? (grep the raw YAML; the styled `hermes config` summary cannot be grepped)
grep -A20 "mcp_servers:" "$(hermes config path)" | head -25

# 3. Can gateway reach LightRAG?
curl -sf http://10.0.0.1:9621/health || echo "LightRAG unreachable"

# 4. Check gateway logs (active profile is telegram; --replace re-binds an existing gateway)
hermes --profile telegram gateway run --replace 2>&1 | head -50
```

**Fix:**
1. ✓ Ensure `API_SERVER_ENABLED=true` in `~/.hermes/.env`
2. ✓ Start gateway (active profile is telegram): `hermes --profile telegram gateway run --replace`
3. ✓ Sync profile: `bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/ensure-orchestration-layer.sh`
4. ✓ Restart WebUI/Workspace

---

### MCP Server Won't Load (Claude Code)

**Symptom:** Tool calls fail, "MCP not available" errors.

**Diagnosis:**
```bash
# Check if MCP process is running
ps aux | grep mcp-obsidian

# Check if path in the active config exists (canonical npm global root is G:\AIStorage\npm\global per C:\Users\alice\.npmrc)
ls /mnt/g/AIStorage/npm/global/node_modules/mcp-obsidian

# Try running MCP directly to see errors
node /mnt/g/AIStorage/npm/global/node_modules/mcp-obsidian/dist/index.js /mnt/d/MyVault
```

**Fix:**
1. ✓ Reinstall MCP: `npm install -g mcp-obsidian` (lands in G:\AIStorage\npm\global per C:\Users\alice\.npmrc prefix)
2. ✓ The active config is `D:\MyVault\.mcp.json` and it already points at `G:\AIStorage\npm\global\node_modules\mcp-obsidian\dist\index.js` — do NOT edit a stale AppData path; verify .mcp.json matches the canonical G:\AIStorage root
3. ✓ Restart Claude Code

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
1. ✓ Verify hook in `settings.json` under `"hooks": {"Stop": ...}`
2. ✓ Run ingest manually: `python3 memoryweaver-spool.py`
3. ✓ Check directory exists: `mkdir -p /mnt/d/MyVault/.claude/Memory/memoryweaver`

---

### RAG Index Not Updating

**Symptom:** Obsidian notes don't appear in search results, local-rag returns stale data.

**Diagnosis:**
```bash
# LightRAG indexing status
curl -s http://10.0.0.1:9621/health | jq '.pipeline_busy'

# Local-RAG last update
ls -la /mnt/d/MyVault/.rag/db/ | tail -5

# Check if Obsidian vault is monitored
grep -i obsidian ~/.claude/settings.json
```

**Fix:**
1. ✓ Reindex LightRAG/vault (server must be up via `start-lightrag.bat`): `[PowerShell] D:\MyVault\.rag\index-vault.bat`. There is NO `POST /rebuild` HTTP endpoint — that path returns 404. If a direct call is needed, the live server exposes `curl -s -X POST http://10.0.0.1:9621/documents/scan`.
2. ✓ Wait for indexing to complete: `curl http://10.0.0.1:9621/health | jq '.pipeline_busy'`
3. ✓ For local-rag, re-ingest via its MCP tools (e.g. `mcp__local-rag__ingest_file` / `ingest_data`) against `BASE_DIR=D:\MyVault`. There is no `local-rag-ingest` CLI binary; local-rag is an MCP server (`mcp-local-rag`). DB at `D:\MyVault\.rag\db`, cache at `D:\MyVault\.rag\cache`.

---

## Configuration Files Reference

| System | Config File | MCP Servers |
|--------|-------------|------------|
| **Claude Code project** | `D:\MyVault\.mcp.json` | obsidian, local-rag, lightrag, graphrag, rag-orchestrator, autoresearch, comfy-video-orchestrator |
| **Claude Code user** | `C:\Users\alice\.claude\settings.json` | startup hooks, permissions, user-level MCP allowances; project `.mcp.json` supplies MyVault MCPs when launched from `D:\MyVault` |
| **Codex user** | `C:\Users\alice\.codex\config.toml` | obsidian, local-rag, graphrag, rag-orchestrator, autoresearch, comfy-video-orchestrator, wordpress |
| **Codex project** | `D:\MyVault\.codex\config.toml` | obsidian, local-rag, graphrag, rag-orchestrator, autoresearch |
| **Hermes WSL root** | `~/.hermes/config.yaml` | obsidian, rag-orchestrator, memoryweaver-mcp, wordpress |
| **Hermes WSL profile** | `~/.hermes/profiles/<profile>/config.yaml` | synced from root; active sessions must reload MCP after changes |
| **Hermes Windows cards** | `C:\Users\alice\.hermes\profiles\<profile>\config.yaml` | profile cards mirrored into WSL runtime as needed |

---

> **context-mode** (added 2026-06-14) is wired per-harness alongside the above: Claude Code via marketplace plugin, Codex via `[mcp_servers.context-mode]` in `config.toml`, Hermes via a `context-mode:` entry in the profile + root `config.yaml`. It is a context-window optimizer (11 `ctx_*` tools), NOT a RAG backend — it sits beside the RAG stack. Full per-harness matrix, store locations, and the Windows-vs-WSL sandbox split: `D:\MyVault\.mcp-servers\CONNECTOR.md` (context-mode section).

## Backbone State & Maturity (verified 2026-06-15)

Snapshot of which tiers are live. Re-check with `/layer-status` or `memoryweaver status`.

- **GraphRAG** (`.rag/graphrag`, wired to the `graphrag` MCP): **populated** — index complete
  (entities / relationships / communities / community_reports present).
- **LightRAG** (:9621) + **local-rag** (`.rag/db`): document RAG corpus live.
- **MemoryWeaver**: **healthy & consistent**. `memoryweaver status` reports Chroma
  explicit=6 + inferred=48 = **54 == fts_index 54**. The Chroma vector tier is populated and
  embedded **locally** — ChromaDB's default ONNX MiniLM via `documents=` upsert, **no
  pay-per-token API** — persisted at `Memory/memoryweaver/chroma/`; the FTS5/BM25 mirror lives
  on the ext4 runtime path. Hybrid recall (vector RRF + BM25) is active. NOTE: a **stale empty**
  `Memory/memoryweaver/chroma.sqlite3` sits *beside* the real `chroma/` directory — ignore it;
  the adapter persists to the subdir (`MW_DATA_DIR/chroma`). Authoritative counts come from
  `memoryweaver status`, not a raw read of that top-level file.
- **mempalace** (Codex `mempalace` MCP): a separate experimental graph subsystem, outside the
  RAG/memory hot path; not audited here.

## Maintenance Checklist

**Weekly:**
- [ ] Run LightRAG health check: `curl http://10.0.0.1:9621/health`
- [ ] Verify gateway is running (discover the live port first — observed 8787 on 2026-06-14, 8642 in older configs): `[PowerShell] Get-NetTCPConnection -State Listen | ? LocalPort -in 8642,8787` then `curl http://127.0.0.1:<live-port>/health`
- [ ] Check memory ingestion: `ls -la /mnt/d/MyVault/.claude/Memory/memoryweaver/`

**Monthly:**
- [ ] Rebuild RAG indexes: `[PowerShell] D:\MyVault\.rag\index-vault.bat` (LightRAG must be running first via `start-lightrag.bat`). There is no `POST /rebuild` HTTP endpoint — that URL returns 404.
- [ ] Sync Hermes profile to WSL: `bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/apply-hermes-profile-to-wsl.sh`
- [ ] Update MCP packages: `npm update -g mcp-obsidian mcp-local-rag`
- [ ] Check disk space in `D:\MyVault\.rag` (should be < 80% full)

**Before major updates:**
1. Backup memory: `cp -r /mnt/d/MyVault/.claude/Memory/memoryweaver ~/backup/`
2. Backup RAG: `cp -r /mnt/d/MyVault/.rag ~/backup/`
3. Document active sessions
4. Run full sync: `bash /mnt/d/MyVault/hermes-agent-self-evolution/scripts/ensure-orchestration-layer.sh`

---

## Key Ports & Services

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| Ollama | 10.0.0.1 | 11434 | Model inference (backend for LightRAG) |
| LightRAG | 10.0.0.1 | 9621 | Vector + graph RAG indexing |
| Hermes WebUI/Gateway | 127.0.0.1 | VERIFY LIVE — observed 8787 (health/root 200) on 2026-06-14; this table historically said 8642, which was NOT listening that day | discover before curling |
| Hermes Dashboard | 127.0.0.1 | 9119 (historic — verify; not listening 2026-06-14) | Hermes control center |
| Hermes Workspace UI | 127.0.0.1 | 3000 (verify) | WebUI frontend |

> Ports drift between Hermes versions/launchers. VERIFY LIVE before trusting a row: `[PowerShell] Get-NetTCPConnection -State Listen | ? LocalPort -in 8642,8787,9119,3000`, or `[WSL/bash] ss -ltnp | grep -E ':(8787|8642|9119|3000)'`. Then health-check the discovered port.

---

## When to Escalate

File an issue if:
- LightRAG consistently returns unhealthy status
- Gateway fails to start after `ensure-orchestration-layer.sh`
- MCP wrapper scripts fail (not a simple path/permission issue)
- Memory ingestion never completes despite configuration being correct
- RAG rebuild hangs for >10 minutes

Before escalating, gather:
```bash
# Logs (active profile is telegram; --replace re-binds an existing gateway)
hermes --profile telegram gateway run --replace 2>&1 | tee /tmp/hermes-gateway.log
curl http://10.0.0.1:9621/health > /tmp/lightrag-status.json

# Config snapshot (copy the raw active config; `hermes config` only prints a styled summary)
cp "$(hermes config path)" /tmp/hermes-config.yaml
cat ~/.hermes/.env > /tmp/hermes-env.txt

# File structure
ls -la /mnt/d/MyVault/.rag > /tmp/rag-structure.txt
ls -la /mnt/d/MyVault/.claude/Memory > /tmp/memory-structure.txt
```

---

**Last updated:** 2026-06-09  
**Maintained by:** MyVault Orchestration Layer Team
