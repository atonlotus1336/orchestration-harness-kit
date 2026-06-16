# RAG + MCP Stack — Connector Key
tags: connector, rag, mcp, orchestrator, backend, integration, key, quickstart, wiring
keywords: connect, plug-in, backend, registry, lightrag, graphrag, memoryweaver, mcp-server, hermes, claude-code, codex, new-backend, python, single-source

> 🔧 **Fixing something (tools missing, RAG/memory down)?** This is a *wiring/config*
> reference, not a fix guide. Go to **`LLM-TRIAGE-CARD.md`** (60-second triage) or
> **`LLM-OPS-RUNBOOK.md` §4** (failure procedures) first. Use THIS doc only to
> *connect / configure* a new tool, script, or backend.

## What this is

One reference for connecting anything to the unified RAG + memory backend stack.
All knowledge systems (LightRAG, GraphRAG, Memoryweaver) feed through a single
orchestrator. New tools, scripts, agents, and apps connect to the orchestrator
only — they never need to know about individual backends.

Important distinction:
- `rag-orchestrator` is the MCP server clients connect to.
- `memoryweaver-mcp` is an optional direct MCP server for Memoryweaver-only tools.
- `wordpress` is a separate action MCP server for example.com WordPress editing. It is not a RAG backend and should stay in the global `mcp_servers` block.
- LightRAG and GraphRAG are backend services/runtime layers behind `rag-orchestrator`.
  Claude Code and Codex may also register direct `lightrag`, `graphrag`, and
  `local-rag` MCP servers for diagnostics or specialized tools, but new apps
  should start with `rag-orchestrator` plus `obsidian` to avoid duplicated
  wiring and split retrieval paths.

## Optimization contract

Canonical routing: `D:\MyVault\.claude\RETRIEVAL-PLAYBOOK.md`. For lowest token
use, clients should route in this order:

1. Authority/conflict: `D:\MyVault\.claude\AUTHORITY-CONSTITUTION.md`.
2. Routing decisions: `D:\MyVault\.claude\RETRIEVAL-PLAYBOOK.md`.
3. Skill/agent: `D:\MyVault\.claude\MASTER-INDEX.md`, then one selected skill.
4. Exact note/path: Obsidian MCP or direct file read.
5. Broad semantic vault search: `rag-orchestrator`.
6. Skill semantic fallback: `skills_index` at `D:\MyVault\.rag\db` via direct
   `local-rag` only when the client exposes collection-aware search; otherwise
   `rag-orchestrator` with a skill-focused query, or the playbook's direct
   Chroma check (`D:\MyVault\.claude\scripts\query-skills-rag.py`).
7. Relationship/community search: GraphRAG through `rag-orchestrator`, or
   direct GraphRAG MCP when available.
8. Method/process guidance after routing:
   `D:\MyVault\.claude\METHODOLOGY-CHARTER.md`.

Do not make full vault traversal the default path. Use broad `rg` or reindexing
only after indexed routes fail or a full audit is explicitly requested.

---

## Stack layout

```
Your App (Hermes / Claude Code / Codex / script / agent)
         |
         v
  rag-orchestrator  (MCP stdio server OR direct Python import)
         |
    +---------+----------+
    |         |          |
Memoryweaver LightRAG  GraphRAG   <-- backends (add more here)
    |         |          |
D:/MyVault/.claude/Memory/memoryweaver   port 9621   D:/MyVault/.rag/graphrag
```

---

## Connect a NEW APP (Hermes / Claude Code / Codex / any MCP client)

Add this block to the app's config:

```yaml
# Hermes  (~/.hermes/config.yaml)
mcp_servers:
  obsidian:
    command: /mnt/c/Program Files/nodejs/node.exe
    args:
      - G:\AIStorage\npm\global\node_modules\mcp-obsidian\dist\index.js
      - D:\MyVault

  rag-orchestrator:
    command: /home/alice/.local/share/rag-orchestrator/.venv/bin/python3.11
    args: [/mnt/d/MyVault/.mcp-servers/rag-orchestrator/server.py]
    env:
      MEMORYWEAVER_DATA_DIR: /mnt/d/MyVault/.claude/Memory/memoryweaver
      MEMORYWEAVER_FTS_DB_PATH: /home/alice/.local/share/memoryweaver-runtime/fts.db
      LIGHTRAG_BASE_URL: http://10.0.0.1:9621

  memoryweaver-mcp:
    command: /home/alice/.local/share/memoryweaver-mcp/venv/bin/python3.11
    args: [/mnt/d/MyVault/.mcp-servers/memoryweaver-mcp/server.py]
    env:
      MEMORYWEAVER_DATA_DIR: /mnt/d/MyVault/.claude/Memory/memoryweaver
      MEMORYWEAVER_FTS_DB_PATH: /home/alice/.local/share/memoryweaver-runtime/fts.db

  wordpress:
    command: /home/alice/.hermes/hermes-agent/venv/bin/python
    args: [/mnt/d/MyVault/.mcp-servers/wordpress/server.py]
    env:
      WORDPRESS_SITE_BASE: https://example.com.com
      WORDPRESS_TOKEN_URL: https://example.com.com/wp-json/api/v1/token
      WORDPRESS_TOKEN_VALIDATE_URL: https://example.com.com/wp-json/api/v1/token-validate
      WORDPRESS_POSTS_URL: https://example.com.com/wp-json/wp/v2/posts
      # Set secrets outside source control:
      # WORDPRESS_JWT_TOKEN: <redacted>
      # or WORDPRESS_USERNAME + WORDPRESS_PASSWORD
```

```json
// Claude Code  (~/.claude/settings.json or D:\MyVault\.mcp.json)
// NOTE: launched via wsl.exe from Windows — env blocks do NOT cross the
// interop boundary (WSLENV unset); the server's built-in defaults apply.
{
  "mcpServers": {
    "rag-orchestrator": {
      "command": "wsl",
      "args": [
        "--",
        "/home/alice/.local/share/rag-orchestrator/.venv/bin/python3.11",
        "/mnt/d/MyVault/.mcp-servers/rag-orchestrator/server.py"
      ]
    }
  }
}
```

Venv note: `.venv` is the canonical environment at
`/home/alice/.local/share/rag-orchestrator/`; `venv` is a compatibility
symlink to `.venv` (old `venv` preserved as `venv.bak-20260612`). Always call
`python3.11` explicitly.

```json
// Codex  (~/.codex/config.toml  or  hooks.json)
// Same pattern as Claude Code above
```

Tools exposed to the app after connecting:
- rag_query(query, mode?, limit?)       — all backends merged
- rag_backend_query(backend, query, fallback?) — single backend, with backup backends when requested backend is offline
- rag_status()                          — health check
- rag_backends_list()                   — list registered backends

WordPress action tools exposed by the `wordpress` MCP server:
- wordpress_validate_token()
- wordpress_site_config()
- wordpress_list_posts(search?, status?, per_page?, page?)
- wordpress_get_post(post_id, context?)
- wordpress_create_post(title, content, status?, excerpt?, slug?, categories?, tags?)
- wordpress_update_post(post_id, ...)
- wordpress_delete_post(post_id, force?)

Hermes runtime tool names are prefixed with `mcp_wordpress_`, for example `mcp_wordpress_wordpress_update_post`.

WordPress post creation defaults to `draft`. Agents should pass `status: publish` only when immediate publishing is intended.

---

## WordPress MCP setup

WordPress is an action MCP server, not a RAG backend. It should be registered in the root Hermes orchestration config and every WSL profile config so profile switches do not drop website-editing access.

Current known-good state:
- Site: `https://example.com.com`
- MCP registration: working when `hermes mcp test wordpress` discovers 7 tools.
- Auth path: username/password environment credentials work for real WordPress REST edits.
- Edit smoke test: Hermes successfully listed recent posts, created draft post ID 554, then moved that same post to trash.
- `WORDPRESS_TOKEN_VALIDATE_URL` currently returns `404 rest_no_route`; do not treat that alone as proof editing is broken. The create/delete smoke test is the authoritative access check.

Canonical source:

```text
D:\MyVault\.mcp-servers\wordpress\server.py
/mnt/d/MyVault/.mcp-servers/wordpress/server.py
```

Register or repair the config entries across all WSL Hermes profiles:

```bash
HERMES_HOME=/home/alice/.hermes/profiles/$(cat ~/.hermes/active_profile 2>/dev/null || echo home) \
  /home/alice/.hermes/hermes-agent/venv/bin/python \
  /mnt/d/MyVault/.mcp-servers/wordpress/register_hermes.py
```

For profile-independent WordPress access across all Hermes agents, run the all-profile sync instead. This writes the `wordpress` MCP server into WSL root config, every WSL profile config, Windows root config, every Windows profile card config, and inserts the managed WordPress guidance block into per-profile `SOUL.md` files:

```bash
/home/alice/.hermes/hermes-agent/venv/bin/python \
  /mnt/d/MyVault/.mcp-servers/wordpress/sync_all_hermes_profiles.py
```

Hermes startup now calls the all-profile sync before orchestration health checks, and the orchestration health checks include `hermes mcp test wordpress`.

The helper writes:

```text
/home/alice/.hermes/config.yaml
/home/alice/.hermes/profiles/*/config.yaml
```

Credential setup is intentionally separate from source control. Configure one of these in the active Hermes profile environment:

```bash
WORDPRESS_JWT_TOKEN=<token>
```

or:

```bash
WORDPRESS_USERNAME=<username>
WORDPRESS_PASSWORD=<password-or-application-password>
```

The WordPress MCP server also reads the root Hermes environment before the active profile environment:

```text
/home/alice/.hermes/.env
/home/alice/.hermes/profiles/<active-profile>/.env
```

Prefer root env storage for shared WordPress credentials. Profile configs and `SOUL.md` guidance are synced across profiles; credentials do not need to be duplicated into every profile for the MCP server to authenticate.

The WordPress MCP server explicitly loads `/home/alice/.hermes/.env` before profile env files, so even sessions launched from `C:\Users\alice\workspace` or a Windows-style HOME should still see shared root credentials.

The server uses these WordPress endpoints:

```text
https://example.com.com/wp-json/api/v1/token
https://example.com.com/wp-json/api/v1/token-validate
https://example.com.com/wp-json/wp/v2/posts
```

Endpoint roles:
- `/wp-json/api/v1/token` issues a Bearer JWT when the token plugin accepts the supplied WordPress username/password.
- `/wp-json/api/v1/token-validate` is configured for compatibility but is not currently registered on the site; expect `404 rest_no_route`.
- `/wp-json/wp/v2/posts` is the working edit endpoint. The MCP server can authenticate and create/update/delete posts through this route.

Verify registration and tool discovery:

```bash
ACTIVE="$(cat ~/.hermes/active_profile 2>/dev/null || echo home)"
grep -A12 -n '^  wordpress:' ~/.hermes/config.yaml
grep -A12 -n '^  wordpress:' ~/.hermes/profiles/$ACTIVE/config.yaml
find ~/.hermes/profiles -maxdepth 2 -name config.yaml -print0 \
  | xargs -0 grep -L '^  wordpress:' || true
HERMES_HOME=~/.hermes/profiles/$ACTIVE hermes mcp list
HERMES_HOME=~/.hermes/profiles/$ACTIVE hermes mcp test wordpress
```

Expected `hermes mcp test wordpress` result:

```text
Connected
Tools discovered: 7
```

Expected end-to-end edit smoke result:

```text
wordpress_site_config: site URL is https://example.com.com
wordpress_list_posts: PASS
wordpress_create_post: PASS, created draft
wordpress_delete_post: PASS, moved draft to trash
wordpress_validate_token: may return 404 rest_no_route; this does not block editing
```

After config or credential changes, reload MCP in the running Hermes session or restart the gateway/Workspace launcher. A stale session may not see newly added MCP servers until reload.

Agent prompt pattern:

```text
Use the WordPress MCP tools to list drafts on example.com.com. Do not publish or delete anything.
```

For a full access check, use this safer prompt:

```text
Use the WordPress MCP tools to check site config, list recent posts, create one temporary draft post titled "Hermes MCP Smoke Test - delete me", then delete/trash that same post. Report only pass/fail, the post id, and HTTP status/error summaries. Do not print usernames, passwords, or tokens.
```

For direct tool references, use Hermes runtime names such as `mcp_wordpress_wordpress_list_posts` and `mcp_wordpress_wordpress_update_post`.

---

## Connect a NEW PYTHON SCRIPT or AGENT

```python
import sys
sys.path.insert(0, "/mnt/d/MyVault/.mcp-servers/rag-orchestrator")
from connect import rag, memory, lightrag, graphrag

# Query everything
results = await rag.query("project architecture decisions")

# Query one backend
decisions = await memory.query("optimization patterns")

# Check what's live
status = await rag.status()
```

One-liner test from terminal (`.venv` is canonical; `venv` is a compatibility symlink to it — always call python3.11 explicitly):
```bash
/home/alice/.local/share/rag-orchestrator/.venv/bin/python3.11 \
  /mnt/d/MyVault/.mcp-servers/rag-orchestrator/connect.py \
  "your query here"
```

---

## Add a NEW BACKEND

> ⚠️ This section EDITS `server.py` — it is for *adding capability*, NOT troubleshooting.
> **Do NOT edit `server.py` to fix a broken stack** (see `LLM-OPS-RUNBOOK.md` Golden Rules / §9 —
> escalate instead). Proceed only when intentionally adding a new backend.

1. Open `/mnt/d/MyVault/.mcp-servers/rag-orchestrator/server.py`
2. Subclass RAGBackend (3 methods):

```python
class MyNewBackend(RAGBackend):
    name = "my-backend"
    description = "What it does"

    async def is_available(self) -> bool:
        # return True when the service is reachable
        return True

    async def query(self, query: str, mode: str = "hybrid", limit: int = 5) -> list[dict]:
        # return list of {score, text, source, metadata}
        return [{"score": 0.9, "text": "...", "source": "...", "metadata": {}}]

    async def status(self) -> dict:
        return {"available": True, "info": "..."}
```

3. Register it (below the existing registrations):

```python
registry.register(MyNewBackend())
```

4. Restart the MCP server. Every connected app gets the new backend automatically.

No app configs need changing. No new MCP connections needed.

---

## Canonical paths (all on G drive)

| Item                    | Path                                                        |
|-------------------------|-------------------------------------------------------------|
| Orchestrator server     | /mnt/d/MyVault/.mcp-servers/rag-orchestrator/server.py      |
| Connector module        | /mnt/d/MyVault/.mcp-servers/rag-orchestrator/connect.py     |
| Memoryweaver MCP server | /mnt/d/MyVault/.mcp-servers/memoryweaver-mcp/server.py      |
| WordPress MCP server    | /mnt/d/MyVault/.mcp-servers/wordpress/server.py             |
| WordPress docs/smoke    | /mnt/d/MyVault/.mcp-servers/wordpress/README.md and smoke_mcp.py |
| Memoryweaver source     | /mnt/d/MyVault/.claude/memoryweaver/                         |
| Memoryweaver data/DB    | /mnt/d/MyVault/.claude/Memory/memoryweaver/                  |
| Obsidian MCP JS entry   | G:\AIStorage\npm\global\node_modules\mcp-obsidian\dist\index.js |
| Obsidian vault arg      | D:\MyVault                                                   |
| LightRAG index          | /mnt/d/MyVault/.rag/lightrag/                                |
| LightRAG service        | Windows service on port 9621; from WSL prefer http://10.0.0.1:9621 or set LIGHTRAG_BASE_URL explicitly |
| GraphRAG index          | /mnt/d/MyVault/.rag/graphrag/                                |
| GraphRAG batch indexer  | D:\MyVault\.rag\index-graphrag.bat                         |
| Orchestrator venv       | /home/alice/.local/share/rag-orchestrator/.venv/ (venv is a symlink to it; always call bin/python3.11) |
| Memoryweaver venv       | /home/alice/.local/share/memoryweaver-mcp/venv/        |

---

## Backend status at a glance

| Backend      | Always on? | Activation                          |
|--------------|------------|-------------------------------------|
| memoryweaver | Yes        | Auto (file-based, no service needed)                 |
| lightrag     | No         | Start `start-lightrag.bat` on Windows; orchestrator reaches it through Windows-host networking from WSL |
| graphrag     | No         | Keep GraphRAG outputs indexed under `D:\MyVault\.rag\graphrag\output`; query via orchestrator's Windows Python wrapper |
| wordpress    | Yes, when credentials are configured | Hermes starts `/mnt/d/MyVault/.mcp-servers/wordpress/server.py` from the active profile; auth comes from `WORDPRESS_JWT_TOKEN` or `WORDPRESS_USERNAME` + `WORDPRESS_PASSWORD` |

---

## Failure modes

- venv uses broken `python` symlink — always call `python3.11` explicitly
- MEMORYWEAVER_DATA_DIR must be set BEFORE importing memoryweaver (import-time resolution)
- Memoryweaver source is repo-local (`/mnt/d/MyVault/.claude/memoryweaver`) and must be injected onto `sys.path`; `python -m memoryweaver` fails in the MCP venv unless you install the package or add `PYTHONPATH`
- `memory_status` / orchestrator status must not shell out to bare `python -m memoryweaver` from the venv unless that module is installed there
- Use one Memoryweaver data path consistently in config and runtime. Canonical casing is the on-disk `/mnt/d/MyVault/.claude/Memory/memoryweaver` (capital M), which all current Hermes configs use; mixed `memory`/`Memory` paths make debugging much harder even when drvfs aliases them (NTFS/drvfs is case-insensitive today, but a case-sensitive mount would split them).
- On this WSL + G-drive setup, FTS-backed SQLite status checks can fail on drvfs with `sqlite3.OperationalError: unable to open database file` even while Chroma semantic search still works. Treat that as a degraded warning path, not proof that MemoryWeaver recall is fully down.
- All clients (Hermes, Claude Code, both Codex configs) now launch the
  `D:\MyVault\.mcp-servers\...` source files directly (as of 2026-06-12). The
  runtime copies under `~/.local/share/...` are kept in sync as fallbacks only;
  if you ever see drift (`md5sum` both), the G-drive source is canonical —
  sync G → runtime, never the reverse.
- Hermes on WSL should launch Obsidian via Windows `node.exe` plus Windows-style paths (`C:\...\index.js`, `D:\MyVault`); passing `/mnt/...` paths to Windows Node breaks module resolution
- Hermes on WSL should launch WordPress via WSL Python plus the G-drive source path `/mnt/d/MyVault/.mcp-servers/wordpress/server.py`. Do not copy WordPress secrets into this repo; put them in the active Hermes profile environment or another approved secret store.
- LightRAG may already be healthy on Windows even when WSL `localhost:9621` fails; check `http://10.0.0.1:9621/health` from WSL or set `LIGHTRAG_BASE_URL=http://10.0.0.1:9621`
- `start-lightrag.bat` must bind `--host 0.0.0.0`, not `127.0.0.1`, or WSL clients cannot reach it through the Windows host gateway
- GraphRAG cannot be treated as ready just because `output/` exists; query requires at least `communities.parquet`, `community_reports.parquet`, `entities.parquet`, `relationships.parquet`, and `text_units.parquet`
- If GraphRAG status shows only `documents.parquet`, `text_units.parquet`, `stats.json`, and `context.json`, the prior index run did not finish; rerun `D:\MyVault\.rag\index-graphrag.bat` and wait for completion
- Running the Windows `graphrag.exe` directly from WSL can fail with a uv trampoline error; prefer the Windows Python runtime with an explicit `sys.path` wrapper if you need to query from WSL
- MCP tool results inside an already-open Hermes session can stay stale after fixing server files; restart Hermes or use `/reload-mcp` so the current process reconnects to the repaired server
- Adding or fixing a backend in `server.py` has no effect until the MCP client restarts or reloads the server process
- If Hermes generations were cancelled while troubleshooting, check `display.busy_input_mode` before blaming MCP/RAG/memory. `interrupt` can cancel in-flight runs or leave you judging stale state from an interrupted session.
- Jonathan prefers `display.busy_input_mode: queue`; do not assume that remains the default after upgrades, profile changes, or rebuilt configs — verify the live `~/.hermes/config.yaml`.
- Using Hermes as regular interactive chat is valid. Use kanban/cron/background execution only when the work must survive session switching, chat churn, or long-running autonomy.

## Startup reconciliation and fallback

Hermes startup runs `D:\MyVault\hermes-agent-self-evolution\scripts\ensure-rag-stack.sh`
through `ensure-hermes-api.sh`.

What it does:
- probes `rag-orchestrator` with retry during startup reconciliation
- treats Memoryweaver as the always-on baseline backend when its Chroma semantic store is healthy; an unavailable SQLite FTS index is a warning because Memoryweaver hybrid queries already degrade to semantic-only
- attempts to auto-start LightRAG via `D:\MyVault\.rag\start-lightrag.bat` when port 9621 is not reachable
- records final backend health in `~/.hermes/logs/rag-stack.log`
- does not auto-run GraphRAG indexing, because `index-graphrag.bat` can take 30-90 minutes
- returns success by default even when RAG is degraded so Workspace can still open with visible warnings; set `REQUIRE_RAG_READY=1` to make degraded Memoryweaver/LightRAG block startup
- only starts GraphRAG indexing when `AUTO_INDEX_GRAPHRAG=1` is explicitly set

Runtime fallback is handled inside `rag-orchestrator`:
- `rag_query` retries each backend and skips unavailable ones while returning the skipped status
- `rag_backend_query(..., fallback=true)` retries the requested backend and queries available backup backends if the requested one is offline
- `rag_status` reports retry attempts, missing GraphRAG outputs, and LightRAG candidate URLs

## Session MCP Registration Gap (Critical Failure Mode)

The most common failure mode on this setup is: **all MCP servers are running and healthy, but Hermes sessions don't expose their tools**. The root cause is a race condition in the startup sequence:

```
Gateway starts ──────────────► Session created ─────► Toolset frozen at creation
                                    │
MCP servers spin up ───► Health checks pass ───────► Too late for existing sessions
```

**Why this happens:**
1. `ensure-hermes-api.sh` launches the gateway as soon as the health endpoint responds.
2. Sessions created in the first few seconds inherit whatever MCP tools were registered at that moment.
3. If `rag-orchestrator` or `lightrag` takes 2-10 seconds longer to become healthy than the gateway startup time, the session will never see those tools — even though `hermes mcp list` later shows them as enabled.
4. There is no "push new MCP tools to active sessions" signal in Hermes. Each session's toolset is fixed at creation.

**Symptoms:**
- `hermes mcp list` shows all servers as `✓ enabled`
- `hermes mcp test <server>` returns `Connected` with tools discovered
- Server processes are running (`ps aux | grep mcp` shows them)
- Backend health probes via Python connector return all green
- But the session only has `mcp_wordpress_*` tools, missing `rag_query`, `rag_backend_query`, `memory_recall`, etc.

**How to confirm this is the problem:**
```bash
# 1. Check if servers are running
ps aux | grep -E 'rag-orchestrator|memoryweaver-mcp|obsidian' | grep -v grep

# 2. Check if servers pass health checks
hermes mcp test rag-orchestrator
hermes mcp test memoryweaver-mcp

# 3. Check if backends are actually healthy (direct probe; .venv is canonical, venv is a symlink to it)
/home/alice/.local/share/rag-orchestrator/.venv/bin/python3.11 -c "
import sys; sys.path.insert(0,'/mnt/d/MyVault/.mcp-servers/rag-orchestrator')
from server import registry, _retry_backend_status
import asyncio, json
async def main():
    for b in registry.all():
        print(f'{b.name}:', json.dumps(await _retry_backend_status(b)))
asyncio.run(main())
"

# 4. Check the orchestration-layer.last for overall result
cat ~/.hermes/logs/orchestration-layer.last
```

If steps 1-3 pass but the session doesn't have the tools, you've hit this gap.

**Available workarounds (no code change needed):**

| Method | How | When to use |
|--------|-----|-------------|
| `/reload-mcp` in chat | Type `/reload-mcp` directly in Hermes web UI or Telegram | Quick, immediate |
| Restart session | Close and reopen the chat / Workspace session | When slash commands unavailable |
| `execute_code` + connect.py | Use the Python connector directly: `from connect import rag, memory, lightrag, graphrag` | Any time — bypasses MCP entirely, works even when MCP tools are missing |
| Gateway restart | `systemctl --user restart hermes-gateway` then reopen session | Full reset, picks up all MCP tools fresh |

**Permanent fix applied by startup scripts:**
The `ensure-orchestration-layer.sh` script now signals the running gateway to reconnect MCP after all servers pass health checks. If the gateway doesn't respond to the signal, it restarts the gateway process. Sessions created after this step will have all MCP tools available.

---

## Hermes-side troubleshooting checklist before declaring the backend broken

1. **Verify MCP registration gap** — Check if servers are running but tools are missing from the session (see "Session MCP Registration Gap" above). This is the most common false-positive failure.
2. Verify the live Hermes config, especially `display.busy_input_mode`.
3. Ask whether the user switched chats, retried, or sent new input while Hermes was still busy.
4. Distinguish cancelled foreground chat runs from true backend health failures.
5. Check `~/.hermes/logs/rag-stack.log` for startup reconciliation results.
6. Check `~/.hermes/logs/orchestration-layer.last` for the full startup health summary.
7. After MCP/RAG/memory fixes, reload MCP or restart Hermes before trusting the next status or tool result from the same session.
8. If the task must outlive the current chat session, move it to a durable mechanism instead of re-testing through an interruptible foreground conversation.
9. **LightRAG timeout during query is not a health failure** — LightRAG's `/health` endpoint may return 200 while actual `/query` calls timeout (especially when GraphRAG subprocess is also querying). Retry the query alone.
10. **GraphRAG UTF-8 decode errors** — Running `graphrag.exe` through Windows Python subprocess from WSL can produce encoding errors on stdout. The `execute_code` path (calling via Python connector) is more reliable than `terminal()` for GraphRAG queries.
11. **Session tools vs. direct connector** — When MCP tools are unavailable, the `execute_code` + `connect.py` path always works as a fallback. If queries return results via the Python connector but not via MCP tools, the issue is in session tool registration, not in the backends.

---

## context-mode (context-window optimizer) — per-harness conditions

`context-mode` (npm package; MCP server exposing 11 `ctx_*` tools) optimizes the
context window: tool/file output runs in a sandboxed subprocess and only summaries
enter context. **It is NOT a RAG backend and does NOT route through
`rag-orchestrator`** — it sits BESIDE the RAG stack. Use RAG
(`rag-orchestrator`/`obsidian`/`local-rag`/LightRAG/GraphRAG) for durable vault
knowledge; use context-mode (`ctx_execute`/`ctx_execute_file`/`ctx_search`) to
contain large, ephemeral in-session tool/file output. Installed 2026-06-14,
v1.0.162. Requires Node ≥22.5 (built-in `node:sqlite`).

Tools (11): `ctx_execute`, `ctx_execute_file`, `ctx_index`, `ctx_search`,
`ctx_fetch_and_index`, `ctx_batch_execute`, `ctx_stats`, `ctx_doctor`,
`ctx_upgrade`, `ctx_purge`, `ctx_insight`.

### Per-harness wiring — each native, NOT one uniform config

| Harness | Install method (native) | Hooks / auto-routing | Verified 2026-06-14 |
|---|---|---|---|
| Claude Code | marketplace **plugin**: `/plugin marketplace add mksglu/context-mode` then `/plugin install context-mode@context-mode` | FULL (all 5 hooks) | run `/context-mode:ctx-doctor` (interactive; user-run) |
| Codex | MCP entry in `C:\Users\alice\.codex\config.toml` (`[mcp_servers.context-mode]`) | partial (Codex hook adapter) | TOML valid + `context-mode` present |
| Hermes | MCP entry in `~/.hermes/profiles/telegram/config.yaml` + root `~/.hermes/config.yaml` | NONE — manual tools only | `hermes mcp test context-mode` → Connected, 11 tools ✓ |

Hermes is **not** a context-mode hook platform → manual MCP tools only (no
auto-routing, no compaction-restore). The `managed:context-mode` blocks in the
telegram `SOUL.md` and in `D:\MyVault\AGENTS.md` nudge the model to use the tools.

### Install locations / reinstall

- Package: `context-mode`; entry: `cli.bundle.mjs`.
- Windows (Claude Code + Codex): `G:\AIStorage\npm\global\node_modules\context-mode\cli.bundle.mjs` (npm prefix per `.npmrc`). Reinstall: `[PowerShell] npm install -g context-mode`.
- WSL (Hermes): `/home/alice/.local/lib/node_modules/context-mode/cli.bundle.mjs` (stable, profile-independent prefix). Reinstall: `[WSL/bash] npm install -g --prefix /home/alice/.local context-mode`.

### Exact MCP commands used

Codex (`config.toml`):
```toml
[mcp_servers.context-mode]
command = "node"
args = ['G:\AIStorage\npm\global\node_modules\context-mode\cli.bundle.mjs']
startup_timeout_sec = 120
```

Hermes (`config.yaml`):
```yaml
  context-mode:
    command: /home/alice/.hermes/profiles/telegram/node/bin/node   # ROOT config uses /home/alice/.local/bin/node
    args:
    - /home/alice/.local/lib/node_modules/context-mode/cli.bundle.mjs
```
WSL has no system node — the only node is each Hermes profile's bundled
`~/.hermes/profiles/<profile>/node/bin/node` (v22.22.3). The telegram entry pins
that node; the root entry uses `~/.local/bin/node` (symlink → telegram node). If a
DIFFERENT profile is activated and context-mode fails to launch, repoint its config
at that profile's node.

### Data store — per-harness local (intentional, not shared)

context-mode auto-detects platform and stores SQLite under the detected platform dir:
- Windows (Claude Code + Codex share one HOME): `C:\Users\alice\.claude\context-mode\` — NTFS, safely shared between the two Windows harnesses.
- WSL (Hermes): `/home/alice/.claude/context-mode/` — native, separate.
NOT shared across the Windows↔WSL boundary on purpose: one SQLite file written by
all three over **drvfs** would hit the same `unable to open database file` FTS
fragility that already degrades Memoryweaver, plus writer-lock contention. The data
is KB-scale and mostly per-session, so sharing buys no efficiency — only overhead.

### Sandbox runtime split (per-harness — important)

`ctx_execute` runs scripts in the runtime that launched the server: Claude Code /
Codex → **Windows** node → Windows python/PowerShell; Hermes → **WSL** node → Linux
bash/python3. Same tool NAMES everywhere, different execution environment — a
`ctx_execute` shell/python script is NOT portable across harnesses.

### Health check / troubleshooting

- Health: `[PowerShell] node G:\AIStorage\npm\global\node_modules\context-mode\cli.bundle.mjs doctor`; `[WSL/bash] /home/alice/.local/bin/context-mode doctor`; in-session `ctx_doctor`; Claude Code plugin `/context-mode:ctx-doctor`.
- Hermes registration: `[WSL/bash] HERMES_HOME=~/.hermes/profiles/<active> hermes mcp test context-mode` → expect Connected + 11 tools.
- `node <22.5` → server won't start; check `node --version` in the launching runtime.
- Tools missing in a session after wiring → reload MCP / restart the harness (same session-registration-gap rule as the rest of the stack).
- Upgrade: `npm install -g context-mode` (Win) / `npm install -g --prefix /home/alice/.local context-mode` (WSL), or `ctx_upgrade`.
