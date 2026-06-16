# REPLICATION-GUIDE.md — Portable Memory-Retrieval & Context-Savings Orchestration

> **Purpose:** rebuild *this machine's* shared-memory + context-savings orchestration
> on **any other system**, for anyone. Self-contained — copy this file out of the vault
> and follow it. Everything machine-specific is a **`<PLACEHOLDER>`** you fill in once
> (§0); the rest is OS-agnostic except where a step is tagged **[WSL]**, **[Linux]**, **[macOS]**.
>
> Scope = the transferable gem: a **canonical memory store + hybrid recall + MCP tools +
> read-on-write digest + per-harness capture**, optionally fronted by RAG and a skill index.
> It deliberately EXCLUDES this vault's personal content (skills library, business OS,
> WordPress, model providers) — see §6.

---

## 0. Parameters — fill these in ONCE, then substitute everywhere

| Placeholder | What it is | This machine's value (example) |
|---|---|---|
| `<USER>` | OS username | `alice` |
| `<VAULT>` | project/vault root (native path) | `D:\MyVault` |
| `<VAULT_NIX>` | same root as a POSIX path | `/mnt/d/MyVault` *(WSL)* — on Linux/macOS `<VAULT>` already is POSIX |
| `<STORE>` | canonical memory dir | `<VAULT_NIX>/.claude/Memory/memoryweaver` |
| `<FTS_DB>` | FTS index on a **fast/native** fs (NOT the vault drive) | `/home/<USER>/.local/share/memoryweaver-runtime/fts.db` |
| `<VENV>` | python venv for the MCP server | `/home/<USER>/.local/share/memoryweaver-mcp/venv` |
| `<PY>` | the venv's python 3.11 | `<VENV>/bin/python3.11` |
| `<RAG_HOST>` | host for LightRAG/Ollama (RAG front-door only) | `10.0.0.1` |

> **Golden rule:** the store and the FTS index must live on different filesystems only
> if your store sits on a slow/network mount (see §3.3). On a normal local disk, put both
> wherever — the split is a Windows+WSL drvfs workaround, not a core requirement.

---

## 1. Dependency inventory (exact)

| Component | Version / source | Needed for |
|---|---|---|
| Python | **3.11** | the memory package + MCP server |
| `chromadb` | **1.5.9** (pinned — newer may change the client API) | vector store |
| `mcp` (Python SDK) | latest | the stdio MCP server |
| `filelock` | latest | single-writer safety |
| MemoryWeaver package | the `memoryweaver/` source (this repo) | the engine |
| MCP server | `memoryweaver-mcp/server.py` (this repo) | exposes 9 tools |
| A harness | Claude Code **≥2.1.139** (for native `/goal`), and/or Codex, and/or Hermes | the agent that calls memory |
| *(optional)* LightRAG + Ollama | LightRAG on `:9621`, Ollama on `:11434` | RAG front-door semantic doc search |
| *(optional)* Node | for `obsidian` / `local-rag` MCPs | vault + doc RAG |

**Core is just the first six rows.** Everything else is optional enrichment.

---

## 2. Platform matrix — what's portable vs specific

| Concern | Portable core | **[WSL]** Windows+WSL | **[Linux]** / **[macOS]** |
|---|---|---|---|
| Store (Chroma) | local dir | on drvfs `G:` (tolerated; regenerable) | local dir, no special handling |
| FTS index | local file | **redirect to ext4** `/home/<USER>/...` (drvfs SQLite WAL is fragile) | same dir as store is fine |
| Writers | one process domain | **all writers WSL-side** (one filelock domain) — never write the drvfs SQLite from Windows + WSL concurrently | single domain natively |
| Paths in configs | `<PLACEHOLDER>` | Windows native for Windows harnesses, `/mnt/...` for WSL procs | POSIX everywhere |
| Home junction | n/a | WSL `~/.claude` may be junctioned to the vault; **Windows `~` is separate** (caused a real divergence — pin `MEMORYWEAVER_DATA_DIR`) | n/a |

> **The single most important portability lesson from this build:** every memory writer
> must pin `MEMORYWEAVER_DATA_DIR` (+`MEMORYWEAVER_FTS_DB_PATH`) to the canonical paths, or
> a harness whose default `~` differs writes to a dead store. Never rely on the package default.

---

## 3. Setup steps (parameterized)

### 3.1 Canonical store + fast-fs FTS
```bash
mkdir -p <STORE>
mkdir -p "$(dirname <FTS_DB>)"        # [WSL] put this on ext4, NOT the vault drive
```

### 3.2 Install the engine
```bash
python3.11 -m venv <VENV>
<PY> -m pip install "chromadb==1.5.9" mcp filelock
# make the memoryweaver package importable (copy it, or add its dir to sys.path —
# the MCP server inserts MEMORYWEAVER_SOURCE_DIR onto sys.path at startup)
```

### 3.3 One env contract (every component uses these)
```bash
export MEMORYWEAVER_DATA_DIR=<STORE>
export MEMORYWEAVER_FTS_DB_PATH=<FTS_DB>
```
`_paths.py` resolves `MW_DATA_DIR` from `MEMORYWEAVER_DATA_DIR` (else `~/.claude/Memory/memoryweaver`).
The MCP server hardcodes these same defaults, so an unconfigured launch still hits canonical.

### 3.4 Seed + verify the store
```bash
cd <VAULT_NIX>/.claude/memoryweaver           # the package dir
<PY> -m memoryweaver migrate --sessions ... --decisions ...   # optional: import existing notes
<PY> -m memoryweaver sync-fts                  # build BM25 index from Chroma
<PY> -m memoryweaver status                    # expect explicit+inferred counts == fts_index
```

### 3.5 MCP server (the 9 tools)
Launch command — **identical string in every harness config** so one server, one store:
```
<PY>  <VAULT_NIX>/.mcp-servers/memoryweaver-mcp/server.py
```
Tools exposed: `memory_recall, memory_browse, memory_status, memory_stub_list, memory_fetch,
memory_derive, memory_synthesize, memory_revoke, memory_harvest_links`.
> [WSL] from a Windows harness, set `command = "wsl"`, `args = ["<PY>", "<server.py>"]`.
> [Linux/macOS] set `command = "<PY>"`, `args = ["<server.py>"]`.

### 3.6 Context-savings hooks (the read-on-write digest)
Two tiny scripts (this repo's `hooks/`):
- **Stop hook** `memory-digest-write.sh` → `<PY> -m memoryweaver digest` (writes `<STORE>/recent-digest.json`, top-N non-quarantined stubs). Pin the env.
- **SessionStart hook** `memory-digest.py` → reads that JSON, emits it as `additionalContext`. **No chromadb import on the read path** → ~0ms, the context-savings win.

### 3.7 Per-harness capture (write-on-stop)
A Stop hook that spools session notes → `memoryweaver spool` → `memoryweaver ingest`, env pinned, run via `<PY>` (WSL-side on Windows). Mirror `stop-wrap-up.py`.

### 3.8 Wire each harness (read + write before/after work)
- **Claude Code:** `settings.json` `hooks.SessionStart` += digest reader; `hooks.Stop` += digest writer + spool; `mcpServers.memoryweaver` = the §3.5 command.
- **Codex:** `config.toml` `[mcp_servers.memoryweaver-mcp]` = §3.5 (gate `memory_revoke` behind `approval_mode="approve"`); `hooks.json` SessionStart/Stop.
- **Hermes:** `config.yaml` `mcp_servers.memoryweaver-mcp` = §3.5; `prefill_messages_file` = a 2-line few-shot priming "consult memory first" (fail-safe, read per-call).

### 3.9 *(optional)* RAG front-door + skill index
- **rag-orchestrator** merges memory + LightRAG + GraphRAG behind one `rag_query` (so harnesses without the direct MCP still get recall). Needs `<RAG_HOST>` services.
- **Skill index:** a generated `MASTER-INDEX` (discovery) + a hand-kept `SKILL-INTEGRATION` (coordination) + a `skills_index` RAG collection. Replicable but vault-specific; optional.

---

## 4. Verification (smoke test after each stage — these are the exact checks used to build this)
```bash
# store + FTS aligned
<PY> -m memoryweaver status        # explicit+inferred == fts_index, 0 errors
# digest round-trips
<PY> -m memoryweaver digest && test -f <STORE>/recent-digest.json
# MCP server registers all 9 tools + status reads canonical
<PY> -c "import importlib.util,asyncio; s=importlib.util.spec_from_file_location('m','<VAULT_NIX>/.mcp-servers/memoryweaver-mcp/server.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(len(asyncio.run(m.list_tools())))"   # -> 9
# end-to-end: in the harness, run memory_status -> see your record counts + data_dir == <STORE>
```

---

## 5. Why this saves context/tokens (the transferable principle)
1. **Two-tier retrieval:** `memory_stub_list` returns ~50–100-token stubs; fetch full text only by id. Triage cheap, load deep rarely.
2. **Read-on-write digest:** session starts with a recent-memory snapshot injected as a tiny JSON (no model, no chromadb) instead of re-explaining context.
3. **Non-generative derivation:** `derive`/`synthesize` build index/rollup memories deterministically (no LLM) — provenance-cited, so recall stays grounded.
4. **Hybrid recall (BM25 + vector, RRF):** keyword precision + semantic reach in one query; degrades to semantic-only if FTS is cold.
5. **Quarantine, not delete:** untrusted/uncited memories are flagged, not surfaced — keeps injected context trustworthy.

---

## 6. ⛔ HARD EXCLUSIONS — replicate the SYSTEM, never the DATA

This guide rebuilds the **system** (vault structure, MCP/RAG wiring, memory engine). It must
**NEVER** duplicate your private **content**. Copying any of the following into someone else's
setup is a privacy / proprietary violation.

**The boundary is structural, not a name list:** everything proprietary lives under
**`<VAULT>/Projects/`** (your project data) plus the **populated store**; everything shared is
orchestration-layer code/docs that live *outside* `Projects/`. Creative projects (e.g. a Ren'Py
SDK folder) sit outside the orchestration layer entirely, so they're never in scope. The kit
enforces this by **allowlist** — only named orchestration code is copied, so `Projects/` is
never a copy candidate.

**NEVER COPY — proprietary / personal:**
- **`<VAULT>/Projects/` — HARD RULE, no exceptions.** Your private projects. Off-limits in
  full; never read, copy, index, or reference their contents in a shared artifact.
- **The populated memory store** — `<STORE>/chroma`, `<STORE>/*.json`, the FTS db. These hold
  real memories/decisions. **Recipients start with an EMPTY store** and grow their own.
- Personal vault content: memory data, decisions / session logs, research notes, and any
  private working folders, notes, and canvases.
- Secrets & accounts: site/API credentials, model-provider keys, RAG host IPs, personal
  harness profiles, and any self-evolution / automation scripts.

**COPY — the portable system only:**
- the `memoryweaver/` package (code) · `memoryweaver-mcp/server.py` · the `hooks/` scripts (templated)
  · this guide · the env contract · the verification tests · an **empty** vault skeleton.

**ASSEMBLY RULE — ALLOWLIST, never denylist.** Build any shareable bundle by copying ONLY the
named system files above. **NEVER** "copy `<VAULT>` then delete `Projects/`" — the vault top level
interleaves system and personal content, so deny-listing risks leaking private data on a single
mistake. The bundle/bootstrap in this repo enforce this in code (`export/memory-orchestration-kit/`).

---

## 7. Quick replicability self-test (did it work?)
A fresh machine is at parity when: `memory_status` shows your seeded counts with `fts_index`
equal; a new session shows the read-on-write digest; the harness can `memory_recall` and
`memory_derive`; and a second harness reading the **same** `<STORE>` sees the same records.
