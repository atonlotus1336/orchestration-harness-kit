# RAG Orchestrator

Single MCP server unifying GraphRAG and Memoryweaver.
Wired into Hermes, WSL Claude Code, WSL Codex, Windows Claude Code, and Windows Codex.

## Tools exposed

- `gateway_query` - unified endpoint with per-call context, retry/circuit policy, degraded truth, and recovery metadata
- `gateway_status` - unified truth-state status for the gateway and all backends
- `rag_query` - compatibility alias for merged multi-backend queries
- `rag_backend_query` - compatibility alias for targeted backend queries with fallback truth
- `rag_status` - compatibility alias for unified backend status
- `rag_backends_list` - list registered backends, policy, and remediation hints

`server.py --status-json` is the non-MCP probe path used by orchestration audit and troubleshooting.
Harness launch wrappers live beside it:

- `launch-codex.sh`
- `launch-claude.sh`
- `launch-hermes.sh`

Those wrappers pin `MyVault_HARNESS` and workspace defaults so the request context is stable even when the MCP client does not pass an explicit `context` object.

## Adding a new backend

1. Subclass `RAGBackend` in `server.py` and implement `is_available()`, `query()`, and `status()`.
2. Call `registry.register(MyBackend())`.
3. Add registry, policy, and remediation metadata under `D:\MyVault\.mcp-servers\orchestration-contracts\`.

## Backend status

| Backend | Source | Requires |
|---|---|---|
| memoryweaver | `D:\MyVault\.claude\memory\memoryweaver` | imports healthy; FTS lag is degraded, not down |
| graphrag | `D:\MyVault\.rag\graphrag\output` | complete parquet outputs |

## Runtime policy

- Retry, timeout, fallback, circuit-breaker, and bounded auto-recovery policy:
  `D:\MyVault\.mcp-servers\orchestration-contracts\backend-policy.json`
- Backend inventory and fallback chains:
  `D:\MyVault\.mcp-servers\orchestration-contracts\gateway-registry.json`
- Manual scripted remediation catalog:
  `D:\MyVault\.mcp-servers\orchestration-contracts\backend-remediation.json`
- Operator remediation runner:
  `D:\MyVault\.mcp-servers\remediation\Invoke-MyVaultBackendRemediation.ps1`

## Venv

`/home/alice/.local/share/rag-orchestrator/.venv`

Expected deps: `mcp`, `httpx`, `chromadb`, Memoryweaver runtime deps.
