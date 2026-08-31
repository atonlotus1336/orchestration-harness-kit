#!/usr/bin/env python3.11
"""
RAG Orchestrator - Programmatic Connector
==========================================
One import to access all backends directly (no MCP overhead).
Use this for scripts, hooks, agents, or any Python tool that
needs to query the knowledge stack without going through MCP.

Usage:
    from connect import rag, memory, graphrag

    # Query all backends at once
    results = await rag.query("project architecture decisions")

    # Query a specific backend
    results = await memory.query("optimization patterns")

    # Check what's alive
    status = await rag.status()

Adding a new backend:
    1. Subclass RAGBackend in server.py
    2. Call registry.register(YourBackend()) in server.py
    3. Import it here:  your_backend = registry.get("your-backend-name")
"""

import os
import sys

# Canonical paths - all from G drive
MCP_ROOT       = "/mnt/d/MyVault/.mcp-servers"
MEMORYWEAVER_SRC = "/mnt/d/MyVault/.claude/memoryweaver"
MEMORYWEAVER_DATA = "/mnt/d/MyVault/.claude/Memory/memoryweaver"
GRAPHRAG_ROOT  = "/mnt/d/MyVault/.rag/graphrag"
VENV_PYTHON    = "/home/alice/.local/share/rag-orchestrator/.venv/bin/python3.11"

# Bootstrap environment
os.environ.setdefault("MEMORYWEAVER_DATA_DIR", MEMORYWEAVER_DATA)
sys.path.insert(0, MEMORYWEAVER_SRC)
sys.path.insert(0, f"{MCP_ROOT}/rag-orchestrator")

# Import registry and all backends from server
from server import registry, MemoryweaverBackend, GraphRAGBackend

# Named handles for direct use
memory   = registry.get("memoryweaver")
graphrag = registry.get("graphrag")


class OrchestratorHandle:
    """Thin wrapper over the full registry for unified queries."""

    async def query(self, query: str, mode: str = "hybrid", limit: int = 5) -> dict:
        """Query all available backends, merge and rank results."""
        all_results = []
        skipped = []
        for backend in registry.all():
            if not await backend.is_available():
                skipped.append(backend.name)
                continue
            results = await backend.query(query, mode=mode, limit=limit)
            for r in results:
                r["backend"] = backend.name
                all_results.append(r)
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {
            "query": query,
            "results": all_results,
            "backends_queried": list({r["backend"] for r in all_results}),
            "skipped": skipped,
            "total": len(all_results),
        }

    async def status(self) -> dict:
        """Check availability of all backends."""
        return {b.name: await b.status() for b in registry.all()}

    async def backends(self) -> list[str]:
        """List all registered backend names."""
        return [b.name for b in registry.all()]

    def register(self, backend):
        """Register a new backend at runtime."""
        registry.register(backend)


# The main handle
rag = OrchestratorHandle()


# ------------------------------------------------------------
# CLI quick-test: python3.11 connect.py "your query here"
# ------------------------------------------------------------
if __name__ == "__main__":
    import asyncio, json, sys

    async def main():
        if len(sys.argv) < 2:
            print(json.dumps(await rag.status(), indent=2))
        else:
            query = " ".join(sys.argv[1:])
            print(json.dumps(await rag.query(query), indent=2))

    asyncio.run(main())
