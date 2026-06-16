# RAG Orchestrator

Single MCP server unifying LightRAG + GraphRAG + Memoryweaver.
Wired into: Hermes, WSL Claude Code, WSL Codex, Windows Claude Code, Windows Codex.

## Tools exposed

- `rag_query` — query all available backends, merged results
- `rag_backend_query` — query one specific backend directly
- `rag_status` — check which backends are live
- `rag_backends_list` — list registered backends + descriptions

## Adding a new backend

1. Subclass `RAGBackend` in server.py — implement `is_available()`, `query()`, `status()`
2. Call `registry.register(MyBackend())` at the bottom of server.py
3. That's it — it's automatically included in `rag_query` and `rag_status`

## Backend status

| Backend      | Source                            | Requires                        |
|--------------|-----------------------------------|---------------------------------|
| memoryweaver | D:/MyVault/.claude/Memory/         | always available                |
| lightrag     | D:/MyVault/.rag/lightrag/          | start-lightrag.bat running      |
| graphrag     | D:/MyVault/.rag/graphrag/output/   | vault indexed (index-graphrag.bat) |

## Venv

~/.local/share/rag-orchestrator/.venv
deps: mcp, httpx, pydantic, chromadb, memoryweaver
