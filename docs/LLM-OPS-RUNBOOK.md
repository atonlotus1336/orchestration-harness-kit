# MyVault LLM Ops Runbook

Audience: ANY model operating on this machine — Claude (any tier), GPT,
Gemini, or local/open-source LLMs — doing setup, troubleshooting, or recovery
of the MyVault RAG/MCP/memory stack. This runbook assumes no prior context and
no special reasoning ability. Follow procedures literally. Run the
verification command after every action. If a verification fails twice, stop
and report (see Section 9).

Shell labels: `[PowerShell]` = Windows PowerShell. `[WSL/bash]` = inside WSL
Ubuntu (`wsl -- bash -c "<command>"` from Windows). `[Either]` = both work.

> Lost or in a hurry? Start at `D:\MyVault\.claude\LLM-TRIAGE-CARD.md` (60-second symptom->action triage), then return here for the literal steps.

---

## 1. System Map

Three AI harnesses (Claude Code, Codex, Hermes) share one backend stack:

- `rag-orchestrator` — main MCP server merging all RAG backends. Source of
  truth: `D:\MyVault\.mcp-servers\rag-orchestrator\server.py` (run from WSL).
- Backends behind it: Memoryweaver (memories, always-on, file-based),
  LightRAG (vault semantic RAG, Windows service on port 9621),
  GraphRAG (relationship queries over prebuilt parquet files).
- `obsidian` MCP — exact note reads from the vault `D:\MyVault`.
- `local-rag` MCP — general vault document corpus (ChromaDB at
  `D:\MyVault\.rag\db`).
- `wordpress` MCP — site editing for example.com.com (action server, not RAG).

### Path table (canonical)

| Item | Path |
|---|---|
| Vault root | `D:\MyVault` (WSL: `/mnt/d/MyVault`) |
| rag-orchestrator source | `D:\MyVault\.mcp-servers\rag-orchestrator\server.py` |
| rag-orchestrator venv | `/home/alice/.local/share/rag-orchestrator/.venv` (`venv` is a symlink to it; always call `bin/python3.11`) |
| memoryweaver-mcp source | `D:\MyVault\.mcp-servers\memoryweaver-mcp\server.py` |
| Memoryweaver data | `D:\MyVault\.claude\Memory\memoryweaver` (capital M is canonical) |
| LightRAG index | `D:\MyVault\.rag\lightrag` — service start: `D:\MyVault\.rag\start-lightrag.bat` |
| GraphRAG outputs | `D:\MyVault\.rag\graphrag\output` — rebuild: `D:\MyVault\.rag\index-graphrag.bat` (30–90 min; NEVER auto-run) |
| Chroma DB (local-rag + skills_index) | `D:\MyVault\.rag\db` |
| npm global root (canonical) | `G:\AIStorage\npm\global` (per `C:\Users\alice\.npmrc`) |
| Claude Code MCP configs | `D:\MyVault\.mcp.json` (project) and `C:\Users\alice\.claude\settings.json` (user) |
| Codex MCP configs | `C:\Users\alice\.codex\config.toml` (Windows), `/home/alice/.codex/config.toml` (WSL), `D:\MyVault\.codex\config.toml` (project) |
| Hermes homes | `/home/alice/.hermes` (WSL CLI), `/home/alice/.hermes/profiles/<active>` (profile), `D:\MyVault\hermes-agent\.hermes-home` (WebUI launcher) |
| Hermes secrets | `<hermes-home>/.env` (e.g. `API_SERVER_KEY`) — never in config.yaml |
| Skills: canonical shared | `D:\MyVault\.claude\skills` (`~/.claude/skills` is a junction to it) |
| Skills: Codex runtime | `C:\Users\alice\.codex\skills` (real dir, edit Codex-only skills here) |
| Skills: backup mirror | `D:\MyVault\.codex\skills` — sync: `D:\MyVault\.claude\scripts\sync-codex-skills.ps1` |

---

## 2. Golden Rules (read before any change)

1. G-drive files under `D:\MyVault\.mcp-servers\` are the SOURCE OF TRUTH for
   MCP server code. Runtime copies under `/home/alice/.local/share/` are
   fallbacks. If they differ, sync G → runtime. Never edit the runtime copy.
2. NEVER run GraphRAG indexing automatically. Only run
   `D:\MyVault\.rag\index-graphrag.bat` when a human explicitly asks.
3. NEVER print, echo, or copy secret values (API keys, passwords, tokens).
   Secrets live in `.env` files. Report only the key NAME and whether it is
   set.
4. After ANY MCP config or server change, the running session will NOT see it.
   Reload MCP or restart the harness, then re-test.
5. Data belongs under `D:\MyVault`. User homes on C: hold only runtime glue
   (interpreters, settings, launchers). Do not create new data stores on C:.
6. Trust fresh command output over anything written in docs, memory, or chat
   history. If a doc number disagrees with a live status call, the live call
   wins.
7. Env blocks on MCP entries launched via `wsl` from Windows DO NOT reach the
   Linux process (WSLENV is unset). Do not "fix" behavior by editing those env
   blocks; the server's built-in defaults apply.

---

## 3. Health Check (run first for ANY problem)

Step 1 — full read-only audit:

```
[PowerShell] powershell -ExecutionPolicy Bypass -File "D:/MyVault/.mcp-servers/audit-orchestration.ps1"
```

Expected: every "Canonical Paths" line says OK; LightRAG `status: healthy`;
all six GraphRAG parquet lines OK; MCP servers listed as Connected.

Step 2 — backend status (works even when MCP tools are missing from the
session):

```
[WSL/bash] /home/alice/.local/share/rag-orchestrator/.venv/bin/python3.11 -c "
import sys, asyncio, json
sys.path.insert(0, '/mnt/d/MyVault/.mcp-servers/rag-orchestrator')
from server import registry, _retry_backend_status
async def main():
    for b in registry.all():
        print(b.name, json.dumps(await _retry_backend_status(b))[:300])
asyncio.run(main())
"
```

Expected: `memoryweaver`, `lightrag`, `graphrag` each print
`"available": true`.

Interpretation rules:
- LightRAG `available: false` → go to 4.1.
- GraphRAG missing parquet outputs → go to 4.2.
- Memoryweaver sqlite/FTS error but Chroma works → go to 4.3 (degraded, not
  down).
- Everything healthy but a harness lacks tools → go to 4.4.

---

## 4. Failure Procedures

### 4.1 LightRAG unreachable

1. `[PowerShell]` check service: open `http://127.0.0.1:9621/health` via
   `Invoke-WebRequest http://127.0.0.1:9621/health` — if it answers, LightRAG
   is up; the problem is the client URL.
2. If not running: `[PowerShell] D:\MyVault\.rag\start-lightrag.bat`
   (it must bind `--host 0.0.0.0`, not 127.0.0.1, or WSL clients cannot reach
   it).
3. From WSL, the working URL is `http://10.0.0.1:9621` (Windows host
   gateway), not `localhost`. Verify:
   `[WSL/bash] curl -s http://10.0.0.1:9621/health`
4. A `/health` 200 with a later `/query` timeout is NOT a health failure —
   retry the query alone.

### 4.2 GraphRAG not ready

1. Check outputs exist:
   `[PowerShell] dir D:\MyVault\.rag\graphrag\output\*.parquet`
2. Query needs ALL of: `communities.parquet`, `community_reports.parquet`,
   `entities.parquet`, `relationships.parquet`, `text_units.parquet`.
3. If only `documents.parquet`/`text_units.parquet`/`stats.json` exist, a
   prior index run did not finish. Report this; only rebuild
   (`D:\MyVault\.rag\index-graphrag.bat`, 30–90 min) on explicit human request.
4. GraphRAG queries from WSL can hit UTF-8 errors via terminal; the Python
   connector path (Section 3 Step 2 style) is more reliable.

### 4.3 Memoryweaver degraded (sqlite FTS error)

- On this drvfs setup, `sqlite3.OperationalError: unable to open database
  file` for the FTS index can occur while Chroma semantic search still works.
  This is DEGRADED, not down — hybrid queries fall back to semantic-only.
- Hermes configs redirect FTS to
  `/home/alice/.local/share/memoryweaver-runtime/fts.db` via
  `MEMORYWEAVER_FTS_DB_PATH` (native ext4, avoids the drvfs problem). If a
  new Hermes home lacks that env var, add it to the `rag-orchestrator` and
  `memoryweaver-mcp` entries.

### 4.4 Servers healthy but session has no tools (most common failure)

Cause: the session's toolset froze before MCP servers finished registering.
Fix in order, cheapest first:
1. In Hermes chat: type `/reload-mcp`.
2. Close and reopen the session.
3. Bypass MCP with the Python connector (runs even when the session has no tools):
   ```
   [WSL/bash] /home/alice/.local/share/rag-orchestrator/.venv/bin/python3.11 -c "
   import sys, asyncio
   sys.path.insert(0, '/mnt/d/MyVault/.mcp-servers/rag-orchestrator')
   from connect import rag
   print(asyncio.run(rag.query('test')))
   "
   ```
4. Full reset: `[WSL/bash] systemctl --user restart hermes-gateway`, reopen
   the session.

### 4.5 Hermes "cancels generations" / stale results

1. Check `display.busy_input_mode` in the LIVE config for the home actually in
   use (see path table; WebUI launcher uses
   `D:\MyVault\hermes-agent\.hermes-home\config.yaml`). Jonathan requires
   `queue`. `interrupt` cancels in-flight runs when new input arrives.
2. Ask whether the user switched chats or re-sent input mid-generation before
   blaming any backend.
3. After MCP/RAG fixes, results from an already-open session are stale —
   reload MCP first (4.4).

### 4.6 WordPress tools missing in Hermes

1. Verify registration in the ACTIVE profile (not just root):
   `[WSL/bash] cat ~/.hermes/active_profile` then
   `grep -A6 "^  wordpress:" ~/.hermes/profiles/<active>/config.yaml`
2. If missing, run the all-profile sync:
   `[WSL/bash] exec /home/alice/.hermes/hermes-agent/venv/bin/python /mnt/d/MyVault/.mcp-servers/wordpress/sync_all_hermes_profiles.py`
   NOTE: the sync does NOT cover the WebUI launcher home
   (`D:\MyVault\hermes-agent\.hermes-home\config.yaml`) — check and add the
   `wordpress:` block there manually if absent (copy the root config's block).
3. Test: `[WSL/bash] hermes mcp test wordpress` → expect Connected, 7 tools.
4. `wordpress_validate_token` returning `404 rest_no_route` does NOT mean
   editing is broken. The authoritative check is: list posts → create one
   draft titled "smoke test - delete me" → delete it.

### 4.7 Drift check (server code or skills)

```
[WSL/bash] md5sum /home/alice/.local/share/rag-orchestrator/server.py /mnt/d/MyVault/.mcp-servers/rag-orchestrator/server.py
[WSL/bash] md5sum /home/alice/.local/share/memoryweaver-mcp/server.py /mnt/d/MyVault/.mcp-servers/memoryweaver-mcp/server.py
```

Hashes must match per pair. If not:
`[WSL/bash] cp /mnt/d/MyVault/.mcp-servers/<server>/server.py /home/alice/.local/share/<server>/server.py`
(G-drive is canonical; never copy the other direction), then reload MCP.

Skills mirror:
`[PowerShell] powershell -ExecutionPolicy Bypass -File D:\MyVault\.claude\scripts\sync-codex-skills.ps1`

---

## 5. Find the Right Skill (routing for the model itself)

1. Open `D:\MyVault\.claude\MASTER-INDEX.md`. Pick a category from the taxonomy
   table at the top, then match tags/descriptions in that section. Load ONE
   skill file only.
2. Still ambiguous → semantic fallback:
   `[Either] python D:\MyVault\.claude\scripts\query-skills-rag.py "<what you need>" 5`
3. Skills tagged `[CC+Cx]` need Claude Code/Codex tools — do not run them in
   Hermes.
4. Never scan the whole skills folder. Full routing rules:
   `D:\MyVault\.claude\RETRIEVAL-PLAYBOOK.md`.

---

## 6. Rebuild Indexes (only after skills/agents change)

```
[PowerShell] python D:\MyVault\.claude\scripts\build-skills-manifest.py
[PowerShell] python D:\MyVault\.claude\scripts\index-skills-rag.py
```

Verify: header of `D:\MyVault\.claude\MASTER-INDEX.md` shows today's date and
plausible counts; `query-skills-rag.py "debugging" 3` returns results.

---

## 7. New Machine / Rebuilt Runtime Setup

> This section rebuilds **your own** runtime by restoring `D:\MyVault` (same paths,
> same machine/clone). To replicate the memory-retrieval + context-savings stack on a
> **different or foreign system** — parameterized, OS-agnostic, for sharing with others —
> use `D:\MyVault\.claude\REPLICATION-GUIDE.md` instead.

1. Restore `D:\MyVault` (it contains vault, MCP sources, RAG stores, memory,
   skills, the Codex skills mirror, and these docs).
2. Recreate runtime glue:
   - WSL venvs: create `/home/alice/.local/share/rag-orchestrator/.venv`
     and `/home/alice/.local/share/memoryweaver-mcp/venv` (python3.11;
     install `chromadb` and the packages server.py imports; add a `venv`
     symlink → `.venv` for rag-orchestrator).
   - npm prefix: set `C:\Users\alice\.npmrc` to
     `prefix=G:\AIStorage\npm\global`; install `mcp-obsidian`,
     `mcp-local-rag` there.
   - Codex skills: copy `D:\MyVault\.codex\skills` → `C:\Users\alice\.codex\skills`.
   - Claude skills junction: `[CMD] mklink /J C:\Users\alice\.claude\skills D:\MyVault\.claude\skills`
3. Point every MCP config at the G-drive sources (Section 1 path table; full
   examples in `D:\MyVault\.mcp-servers\CONNECTOR.md`).
4. Put secrets in `<hermes-home>/.env`, never in config.yaml.
5. Run Section 6 (rebuild indexes), then Section 3 (health check).

---

## 8. Reference Docs (read on demand)

- Precedence when instructions conflict: `D:\MyVault\.claude\AUTHORITY-CONSTITUTION.md`
- Where to look things up: `D:\MyVault\.claude\RETRIEVAL-PLAYBOOK.md`
- How to work once routed: `D:\MyVault\.claude\METHODOLOGY-CHARTER.md`
- Architecture + maintenance detail: `D:\MyVault\ORCHESTRATION-LAYER.md`
- Full connector/wiring reference + failure modes: `D:\MyVault\.mcp-servers\CONNECTOR.md`
- Replicate the memory/context stack on **another/foreign system** (portable, parameterized): `D:\MyVault\.claude\REPLICATION-GUIDE.md`

## 9. When to Stop and Report

Stop and report to the user (do not improvise) when:
- A verification step fails twice after following the procedure.
- A fix would require deleting data, rotating a secret, rebuilding GraphRAG,
  or editing more than one harness's config at once.
- Live evidence contradicts this runbook — report the contradiction; evidence
  wins, and the runbook needs an update.

When reporting, include: the procedure section you ran, the exact command, the
exact output (secrets redacted), and what you expected instead.
