#!/usr/bin/env python3.11
"""
RAG Orchestrator MCP Server
============================
Single unified MCP server over LightRAG + GraphRAG + Memoryweaver.
Add new backends by subclassing RAGBackend and calling registry.register().
Runs as stdio MCP server - wire it into Hermes, Claude Code, and Codex.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    DEFAULT_MEMORYWEAVER_DATA_DIR = r"D:\MyVault\.claude\memory\memoryweaver"
    DEFAULT_MEMORYWEAVER_FTS_DB_PATH = r"D:\MyVault\.claude\memory\memoryweaver\fts.db"
    MEMORYWEAVER_SOURCE_DIR = r"D:\MyVault\.claude\memoryweaver"
    GRAPHRAG_ROOT = r"D:\MyVault\.rag\graphrag"
else:
    DEFAULT_MEMORYWEAVER_DATA_DIR = "/mnt/d/MyVault/.claude/memory/memoryweaver"
    DEFAULT_MEMORYWEAVER_FTS_DB_PATH = "/home/alice/.local/share/memoryweaver-runtime/fts.db"
    MEMORYWEAVER_SOURCE_DIR = "/mnt/d/MyVault/.claude/memoryweaver"
    GRAPHRAG_ROOT = "/mnt/d/MyVault/.rag/graphrag"
GRAPHRAG_OUTPUT_DIR = f"{GRAPHRAG_ROOT}/output"
GRAPHRAG_WINDOWS_PYTHON = (
    r"C:\Users\alice\AppData\Roaming\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe"
    if IS_WINDOWS
    else "/mnt/c/Users/alice/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe"
)
GRAPHRAG_WINDOWS_SITE_PACKAGES = r"D:\MyVault\.rag\systems\.venv\Lib\site-packages"
GRAPHRAG_WINDOWS_ROOT = r"D:\MyVault\.rag\graphrag"
GRAPHRAG_REQUIRED_OUTPUTS = [
    "communities.parquet",
    "community_reports.parquet",
    "entities.parquet",
    "relationships.parquet",
    "text_units.parquet",
]

# Set memoryweaver data dir and Linux-ext4 FTS DB path before importing.
os.environ["MEMORYWEAVER_DATA_DIR"] = os.environ.get(
    "MEMORYWEAVER_DATA_DIR", DEFAULT_MEMORYWEAVER_DATA_DIR
)
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.environ.get(
    "MEMORYWEAVER_FTS_DB_PATH", DEFAULT_MEMORYWEAVER_FTS_DB_PATH
)
if MEMORYWEAVER_SOURCE_DIR not in sys.path:
    sys.path.insert(0, MEMORYWEAVER_SOURCE_DIR)


def _default_lightrag_base_url() -> str:
    override = os.environ.get("LIGHTRAG_BASE_URL")
    if override:
        return override

    resolv_conf = Path("/etc/resolv.conf")
    try:
        if resolv_conf.exists():
            for line in resolv_conf.read_text().splitlines():
                if line.startswith("nameserver "):
                    host_ip = line.split(None, 1)[1].strip()
                    if host_ip:
                        return f"http://{host_ip}:9621"
    except Exception:
        pass

    return "http://localhost:9621"


DEFAULT_LIGHTRAG_BASE_URL = _default_lightrag_base_url()
BACKEND_RETRY_ATTEMPTS = int(os.environ.get("RAG_BACKEND_RETRY_ATTEMPTS", "2"))
BACKEND_RETRY_DELAY_SECONDS = float(os.environ.get("RAG_BACKEND_RETRY_DELAY_SECONDS", "0.75"))


async def _retry_backend_status(backend: "RAGBackend") -> dict:
    last_status: dict[str, Any] = {}
    attempts = max(1, BACKEND_RETRY_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        try:
            last_status = await backend.status()
            if last_status.get("available"):
                last_status["attempts"] = attempt
                return last_status
        except Exception as exc:
            last_status = {"available": False, "error": str(exc)}
        if attempt < attempts:
            await asyncio.sleep(BACKEND_RETRY_DELAY_SECONDS)
    last_status["attempts"] = attempts
    return last_status


async def _query_with_retry(backend: "RAGBackend", query: str, mode: str, limit: int) -> list[dict]:
    attempts = max(1, BACKEND_RETRY_ATTEMPTS)
    last_results: list[dict] = []
    for attempt in range(1, attempts + 1):
        last_results = await backend.query(query, mode=mode, limit=limit)
        usable = [
            item
            for item in last_results
            if item.get("score", 0) > 0 or not str(item.get("text", "")).startswith("Error:")
        ]
        if usable:
            for item in last_results:
                item.setdefault("metadata", {})
                item["metadata"]["attempts"] = attempt
            return last_results
        if attempt < attempts:
            await asyncio.sleep(BACKEND_RETRY_DELAY_SECONDS)
    for item in last_results:
        item.setdefault("metadata", {})
        item["metadata"]["attempts"] = attempts
    return last_results


class RAGBackend(ABC):
    name: str
    description: str

    @abstractmethod
    async def is_available(self) -> bool:
        """Return True if this backend can service queries right now."""
        ...

    @abstractmethod
    async def query(self, query: str, mode: str = "hybrid", limit: int = 5) -> list[dict]:
        """Return list of {score, text, source, metadata} dicts."""
        ...

    @abstractmethod
    async def status(self) -> dict:
        """Return health/stats dict."""
        ...


class BackendRegistry:
    def __init__(self):
        self._backends: dict[str, RAGBackend] = {}

    def register(self, backend: RAGBackend):
        self._backends[backend.name] = backend

    def all(self) -> list[RAGBackend]:
        return list(self._backends.values())

    def get(self, name: str) -> RAGBackend | None:
        return self._backends.get(name)


registry = BackendRegistry()


class MemoryweaverBackend(RAGBackend):
    name = "memoryweaver"
    description = "Hybrid BM25+semantic search over accumulated session decisions, inferred patterns, and project knowledge"

    def _imports_ok(self) -> tuple[bool, str | None]:
        try:
            import chromadb  # noqa: F401
            from memoryweaver.store.chroma import ChromaDBAdapter  # noqa: F401
            from memoryweaver.store.fts import FTS5Adapter  # noqa: F401
            return True, None
        except Exception as exc:
            return False, str(exc)

    async def is_available(self) -> bool:
        ok, _ = self._imports_ok()
        return ok

    async def query(self, query: str, mode: str = "hybrid", limit: int = 5) -> list[dict]:
        try:
            from memoryweaver.store.chroma import ChromaDBAdapter

            store = ChromaDBAdapter()
            if mode == "semantic":
                results = store.query_semantic(
                    text=query,
                    top_k=limit,
                    include_inferred=True,
                )
            else:
                results = store.query_hybrid(
                    text=query,
                    top_k=limit,
                    include_inferred=True,
                )

            out = []
            for item in results:
                out.append(
                    {
                        "score": item.get("rrf_score", item.get("combined_score", item.get("score", 0))),
                        "text": item.get("document", item.get("text", "")),
                        "source": (item.get("metadata") or {}).get("source_uri", "memoryweaver"),
                        "metadata": item.get("metadata", {}),
                    }
                )
            return out
        except Exception as exc:
            return [{"score": 0, "text": f"Error: {exc}", "source": "memoryweaver", "metadata": {}}]

    async def status(self) -> dict:
        ok, err = self._imports_ok()
        if not ok:
            return {"available": False, "error": err}

        try:
            from memoryweaver.store.chroma import ChromaDBAdapter
            from memoryweaver.store.fts import FTS5Adapter

            spool_dir = Path(os.environ.get("MEMORYWEAVER_DATA_DIR", DEFAULT_MEMORYWEAVER_DATA_DIR)) / "spool"
            store = ChromaDBAdapter()
            explicit_count = store.count("explicit_memories")
            inferred_count = store.count("inferred_memories")
            pending = len(list(spool_dir.glob("*.json"))) if spool_dir.exists() else 0
            done = len(list(spool_dir.glob("*.done"))) if spool_dir.exists() else 0
            errors = len(list(spool_dir.glob("*.error"))) if spool_dir.exists() else 0

            warnings = []
            chroma_total = explicit_count + inferred_count
            fts_count = 0
            try:
                fts = FTS5Adapter()
                fts_count = fts.count()
                if fts_count == 0 and chroma_total > 0:
                    warnings.append("FTS index empty -- run: python -m memoryweaver sync-fts")
                elif fts_count < chroma_total:
                    warnings.append(f"FTS ({fts_count}) < Chroma ({chroma_total}) -- run: python -m memoryweaver sync-fts")
            except Exception as exc:
                warnings.append(f"FTS unavailable; Memoryweaver semantic search remains available: {exc}")

            return {
                "available": True,
                "data_dir": os.environ.get("MEMORYWEAVER_DATA_DIR", DEFAULT_MEMORYWEAVER_DATA_DIR),
                "explicit_memories": explicit_count,
                "inferred_memories": inferred_count,
                "fts_index": fts_count,
                "spool_pending": pending,
                "spool_done": done,
                "spool_errors": errors,
                "warnings": warnings,
            }
        except Exception as exc:
            return {"available": False, "error": str(exc)}


class LightRAGBackend(RAGBackend):
    name = "lightrag"
    description = "Hybrid graph+vector search over the MyVault vault (requires start-lightrag.bat running on Windows on port 9621)"

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.environ.get("LIGHTRAG_BASE_URL") or "http://127.0.0.1:9621"

    def _candidate_base_urls(self) -> list[str]:
        candidates: list[str] = []
        env_url = os.environ.get("LIGHTRAG_BASE_URL")
        if env_url:
            candidates.append(env_url)
        candidates.extend([self.base_url, "http://localhost:9621", "http://127.0.0.1:9621"])
        if IS_WINDOWS:
            return "http://127.0.0.1:9621"

        try:
            route_lines = Path('/proc/net/route').read_text().splitlines()[1:]
            for line in route_lines:
                fields = line.split()
                if len(fields) > 2 and fields[1] == '00000000':
                    gateway_hex = fields[2]
                    octets = [str(int(gateway_hex[i:i+2], 16)) for i in range(0, 8, 2)]
                    gateway_ip = '.'.join(reversed(octets))
                    candidates.append(f'http://{gateway_ip}:9621')
                    break
        except Exception:
            pass
        try:
            for line in Path('/etc/resolv.conf').read_text().splitlines():
                if line.startswith('nameserver '):
                    candidates.append(f"http://{line.split()[1]}:9621")
                    break
        except Exception:
            pass
        deduped = []
        seen = set()
        for url in candidates:
            if url and url not in seen:
                seen.add(url)
                deduped.append(url)
        return deduped

    def _keyword_hints(self, query: str) -> tuple[list[str], list[str]]:
        stopwords = {
            "the", "and", "for", "with", "from", "that", "this", "what", "when", "where",
            "how", "why", "who", "are", "was", "were", "into", "onto", "about", "your",
            "have", "has", "had", "using", "used", "use", "does", "did", "done", "agent",
        }
        tokens: list[str] = []
        seen = set()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,}", query):
            lowered = token.lower()
            if lowered in stopwords or lowered in seen:
                continue
            seen.add(lowered)
            tokens.append(token)
        if not tokens:
            tokens = [query.strip()[:80] or "query"]
        hl_keywords = tokens[:3]
        ll_keywords = tokens[:6]
        return hl_keywords, ll_keywords

    async def _post_query(self, base_url: str, payload: dict, timeout_seconds: float = 20.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(f"{base_url}/query", json=payload)
            response.raise_for_status()
            return response.json()

    def _format_query_results(self, data: dict, base_url: str, *, fallback_context: bool = False) -> list[dict]:
        if isinstance(data, dict) and "response" in data:
            metadata = {"references": data.get("references", []), "base_url": base_url}
            if fallback_context:
                metadata["fallback_context"] = True
            return [
                {
                    "score": 0.8 if fallback_context else 1.0,
                    "text": data.get("response", ""),
                    "source": "lightrag",
                    "metadata": metadata,
                }
            ]
        results = data.get("results", data.get("data", [])) if isinstance(data, dict) else []
        out = []
        for item in results:
            out.append(
                {
                    "score": item.get("score", item.get("similarity", 0)),
                    "text": item.get("content", item.get("text", str(item))),
                    "source": "lightrag",
                    "metadata": {**item.get("metadata", {}), "base_url": base_url},
                }
            )
        return out

    async def _context_fallback(self, base_url: str, query: str, mode: str, limit: int, primary_error: Exception) -> list[dict]:
        hl_keywords, ll_keywords = self._keyword_hints(query)
        fallback_mode = "mix" if mode == "hybrid" else mode
        try:
            data = await self._post_query(
                base_url,
                {
                    "query": query,
                    "mode": fallback_mode,
                    "top_k": limit,
                    "only_need_context": True,
                    "include_references": True,
                    "enable_rerank": False,
                    "hl_keywords": hl_keywords,
                    "ll_keywords": ll_keywords,
                },
                timeout_seconds=12.0,
            )
            return self._format_query_results(data, base_url, fallback_context=True)
        except Exception as fallback_exc:
            return [{
                "score": 0,
                "text": f"Error: primary query failed ({type(primary_error).__name__}: {primary_error}); context fallback also failed ({type(fallback_exc).__name__}: {fallback_exc})",
                "source": "lightrag",
                "metadata": {"base_url": base_url, "hl_keywords": hl_keywords, "ll_keywords": ll_keywords},
            }]

    async def _first_live_base_url(self) -> str | None:
        for url in self._candidate_base_urls():
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    r = await client.get(f"{url}/health")
                    if r.status_code == 200:
                        return url
            except Exception:
                continue
        return None

    async def is_available(self) -> bool:
        return await self._first_live_base_url() is not None

    async def query(self, query: str, mode: str = "hybrid", limit: int = 5) -> list[dict]:
        base_url = await self._first_live_base_url()
        if not base_url:
            return [{
                "score": 0,
                "text": "Error: LightRAG service is not reachable from WSL. Start start-lightrag.bat and ensure it binds to 0.0.0.0:9621, or set LIGHTRAG_BASE_URL.",
                "source": "lightrag",
                "metadata": {"candidates": self._candidate_base_urls()},
            }]
        try:
            data = await self._post_query(
                base_url,
                {
                    "query": query,
                    "mode": mode,
                    "top_k": limit,
                    "include_references": True,
                    "enable_rerank": False,
                },
            )
            return self._format_query_results(data, base_url)
        except Exception as exc:
            return await self._context_fallback(base_url, query, mode, limit, exc)

    async def status(self) -> dict:
        base_url = await self._first_live_base_url()
        if not base_url:
            return {"available": False, "error": "All connection attempts failed", "candidates": self._candidate_base_urls()}
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{base_url}/health")
                return {"available": True, "base_url": base_url, "data": r.json(), "candidates": self._candidate_base_urls()}
        except Exception as exc:
            return {"available": False, "base_url": base_url, "error": str(exc), "candidates": self._candidate_base_urls()}


class GraphRAGBackend(RAGBackend):
    name = "graphrag"
    description = "Community/relationship-aware graph queries over the MyVault vault (requires a complete GraphRAG index)"

    def __init__(self, root: str = GRAPHRAG_ROOT):
        self.root = root
        self.output_dir = os.path.join(self.root, "output")

    def _missing_outputs(self) -> list[str]:
        return [name for name in GRAPHRAG_REQUIRED_OUTPUTS if not os.path.exists(os.path.join(self.output_dir, name))]

    def _runtime_available(self) -> tuple[bool, str | None]:
        if not os.path.exists(GRAPHRAG_WINDOWS_PYTHON):
            return False, f"Missing Windows Python runtime: {GRAPHRAG_WINDOWS_PYTHON}"
        return True, None

    def _stats_summary(self) -> dict[str, Any] | None:
        stats_path = os.path.join(self.output_dir, "stats.json")
        if not os.path.exists(stats_path):
            return None
        try:
            with open(stats_path, "r", encoding="utf-8") as fh:
                stats = json.load(fh)
            return {
                "stats_path": stats_path,
                "num_documents": stats.get("num_documents"),
                "total_runtime": stats.get("total_runtime"),
                "workflows_completed": sorted((stats.get("workflows") or {}).keys()),
            }
        except Exception as exc:
            return {"stats_path": stats_path, "error": str(exc)}

    def _build_wrapper_script(self, method: str, query: str) -> str:
        payload = json.dumps({
            "site_packages": GRAPHRAG_WINDOWS_SITE_PACKAGES,
            "root": GRAPHRAG_WINDOWS_ROOT,
            "method": method,
            "query": query,
        })
        return (
            "import json, runpy, sys; "
            f"cfg=json.loads({json.dumps(payload)}); "
            "sys.path.insert(0, cfg['site_packages']); "
            "sys.path.insert(0, cfg['site_packages'] + r'\\win32'); "
            "sys.path.insert(0, cfg['site_packages'] + r'\\win32\\lib'); "
            "sys.argv=['graphrag','query','--root',cfg['root'],'--method',cfg['method'],cfg['query']]; "
            "runpy.run_module('graphrag', run_name='__main__')"
        )

    async def is_available(self) -> bool:
        missing = self._missing_outputs()
        runtime_ok, _ = self._runtime_available()
        return not missing and runtime_ok

    async def query(self, query: str, mode: str = "local", limit: int = 5) -> list[dict]:
        method = "local" if mode in ("local", "hybrid") else "global"
        missing = self._missing_outputs()
        if missing:
            return [{"score": 0, "text": "Error: GraphRAG index is incomplete. Missing required output parquet files.", "source": "graphrag", "metadata": {"missing_outputs": missing, "root": self.root}}]
        try:
            cmd = [
                GRAPHRAG_WINDOWS_PYTHON,
                "-c",
                self._build_wrapper_script(method, query),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            text = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            combined = text if result.returncode == 0 else (text + ("\n" if text and err else "") + err)
            combined = combined.strip() or f"GraphRAG exited with code {result.returncode}"
            score = 1.0 if result.returncode == 0 else 0
            return [{"score": score, "text": combined, "source": "graphrag", "metadata": {"method": method, "exit_code": result.returncode}}]
        except Exception as exc:
            return [{"score": 0, "text": f"Error: {exc}", "source": "graphrag", "metadata": {}}]

    async def status(self) -> dict:
        missing = self._missing_outputs()
        runtime_ok, runtime_error = self._runtime_available()
        available = not missing and runtime_ok
        hints = []
        if missing:
            hints.append("GraphRAG output is incomplete; rerun sync-graphrag-input.py then index-graphrag.bat")
        if missing and os.path.exists(os.path.join(self.output_dir, "text_units.parquet")):
            hints.append("Partial preprocessing exists, but graph/community parquet outputs were not generated")
        return {
            "available": available,
            "index_path": self.output_dir,
            "index_exists": os.path.exists(self.output_dir),
            "missing_required_outputs": missing,
            "windows_python": GRAPHRAG_WINDOWS_PYTHON,
            "runtime_ok": runtime_ok,
            "runtime_error": runtime_error,
            "stats": self._stats_summary(),
            "hints": hints,
        }


registry.register(MemoryweaverBackend())
registry.register(LightRAGBackend())
registry.register(GraphRAGBackend())

app = Server("rag-orchestrator")


@app.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="rag_query",
            description="Query all available RAG backends (Memoryweaver, LightRAG, GraphRAG) simultaneously. Results are merged and ranked by score. Inactive backends are skipped gracefully.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The query to run across all backends"},
                    "mode": {"type": "string", "enum": ["hybrid", "local", "global"], "default": "hybrid"},
                    "limit": {"type": "integer", "default": 5, "description": "Results per backend"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="rag_backend_query",
            description="Query a single specific RAG backend by name. If fallback is true and the backend is unavailable, available backup backends are queried instead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "backend": {"type": "string", "enum": ["memoryweaver", "lightrag", "graphrag"]},
                    "query": {"type": "string"},
                    "mode": {"type": "string", "default": "hybrid"},
                    "limit": {"type": "integer", "default": 5},
                    "fallback": {"type": "boolean", "default": True},
                },
                "required": ["backend", "query"],
            },
        ),
        types.Tool(
            name="rag_status",
            description="Check availability and health of all registered RAG backends.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="rag_backends_list",
            description="List all registered backends and their descriptions.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "rag_backends_list":
        backends = [{"name": b.name, "description": b.description} for b in registry.all()]
        return [types.TextContent(type="text", text=json.dumps(backends, indent=2))]

    if name == "rag_status":
        statuses = {}
        for backend in registry.all():
            statuses[backend.name] = await _retry_backend_status(backend)
        return [types.TextContent(type="text", text=json.dumps(statuses, indent=2))]

    if name == "rag_backend_query":
        backend_name = arguments.get("backend")
        query = arguments.get("query", "")
        mode = arguments.get("mode", "hybrid")
        limit = arguments.get("limit", 5)
        fallback = arguments.get("fallback", True)
        backend = registry.get(backend_name)
        if not backend:
            return [types.TextContent(type="text", text=f"Unknown backend: {backend_name}")]
        status = await _retry_backend_status(backend)
        if status.get("available"):
            results = await _query_with_retry(backend, query, mode=mode, limit=limit)
            return [types.TextContent(type="text", text=json.dumps({"backend": backend_name, "status": status, "results": results}, indent=2))]

        fallback_results = []
        fallback_statuses = {}
        if fallback:
            for candidate in registry.all():
                if candidate.name == backend_name:
                    continue
                candidate_status = await _retry_backend_status(candidate)
                fallback_statuses[candidate.name] = candidate_status
                if not candidate_status.get("available"):
                    continue
                results = await _query_with_retry(candidate, query, mode=mode, limit=limit)
                for result in results:
                    result["backend"] = candidate.name
                    fallback_results.append(result)
            fallback_results.sort(key=lambda item: item.get("score", 0), reverse=True)

        return [types.TextContent(type="text", text=json.dumps({
            "backend": backend_name,
            "available": False,
            "status": status,
            "fallback_used": bool(fallback_results),
            "fallback_statuses": fallback_statuses,
            "results": fallback_results,
        }, indent=2))]

    if name == "rag_query":
        query = arguments.get("query", "")
        mode = arguments.get("mode", "hybrid")
        limit = arguments.get("limit", 5)

        all_results = []
        skipped = []

        for backend in registry.all():
            status = await _retry_backend_status(backend)
            if not status.get("available"):
                skipped.append({"backend": backend.name, "status": status})
                continue
            results = await _query_with_retry(backend, query, mode=mode, limit=limit)
            for result in results:
                result["backend"] = backend.name
                all_results.append(result)

        all_results.sort(key=lambda item: item.get("score", 0), reverse=True)

        output = {
            "query": query,
            "results": all_results,
            "backends_queried": list({item["backend"] for item in all_results}),
            "skipped": skipped,
            "total": len(all_results),
        }
        return [types.TextContent(type="text", text=json.dumps(output, indent=2))]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
