# system — the memory engine and RAG front-door

**What it is:** the persistent brain. Two services your agents talk to through MCP
(the standard tool protocol all major harnesses speak):

- **`memoryweaver/` + `memoryweaver-mcp/`** — cross-session memory. Stores
  decisions and facts as they happen, recalls them by *hybrid search* (keyword +
  semantic vector, fused). Two-tier retrieval keeps it cheap: agents fetch tiny
  "stubs" first and pull full text only for the hits that matter. Embeddings run
  locally (no per-token API cost). Your store starts **empty** — it fills with
  *your* work, not the author's.
- **`rag-orchestrator/`** — one query endpoint in front of all your knowledge
  backends, so agents ask a single tool instead of juggling five. Reports honest
  `degraded` status when a backend is down instead of pretending health.
- **`tests/`** — the verification suite the installer runs.
- **`build-skills-manifest.py`** — regenerates a searchable index of whatever
  skills *your* machine has.

**Why you'd want it:** without memory, every session starts from zero and your
agent re-learns (or re-breaks) the same things. This is the part that makes an
agent feel cumulative.

## Install just this part

This is the one part with real setup (Python 3.11, a venv, MCP wiring per
harness) — follow **[`../docs/REPLICATION-GUIDE.md`](../docs/REPLICATION-GUIDE.md)
§3** rather than improvising; it covers the placeholders (`__STORE__`,
`__FTS_DB__`), the empty-store initialization, and per-harness registration
(including the Claude Code trap where MCP servers in the wrong config file are
silently ignored). §4 gives the smoke tests that prove it's actually loaded —
"configured" and "running" are different claims.
