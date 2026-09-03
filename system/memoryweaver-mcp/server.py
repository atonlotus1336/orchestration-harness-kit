#!/usr/bin/env python3.11
"""
Memoryweaver MCP stdio server for Hermes.
Exposes memory_recall, memory_browse, and memory_status as tools.
"""
import asyncio
import os
import sys

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

DEFAULT_MEMORYWEAVER_DATA_DIR = "__STORE__"
DEFAULT_MEMORYWEAVER_FTS_DB_PATH = "__STORE__/fts.db"
MEMORYWEAVER_SOURCE_DIR = os.environ.get("MEMORYWEAVER_SOURCE_DIR", "__SRC_MW__")

# Set data dir and FTS DB path before importing memoryweaver.
os.environ["MEMORYWEAVER_DATA_DIR"] = os.environ.get(
    "MEMORYWEAVER_DATA_DIR", DEFAULT_MEMORYWEAVER_DATA_DIR
)
os.environ["MEMORYWEAVER_FTS_DB_PATH"] = os.environ.get(
    "MEMORYWEAVER_FTS_DB_PATH", DEFAULT_MEMORYWEAVER_FTS_DB_PATH
)
if MEMORYWEAVER_SOURCE_DIR not in sys.path:
    sys.path.insert(0, MEMORYWEAVER_SOURCE_DIR)

app = Server("memoryweaver")


def _status_text() -> str:
    from pathlib import Path
    from memoryweaver.store.chroma import ChromaDBAdapter

    store = ChromaDBAdapter()
    explicit_total = store.count("explicit_memories")
    inferred_total = store.count("inferred_memories")
    explicit_count = store.count("explicit_memories", live=True)
    inferred_count = store.count("inferred_memories", live=True)

    spool_dir = Path(os.environ.get("MEMORYWEAVER_DATA_DIR", DEFAULT_MEMORYWEAVER_DATA_DIR)) / "spool"
    pending = len(list(spool_dir.glob("*.json"))) if spool_dir.exists() else 0
    done = len(list(spool_dir.glob("*.done"))) if spool_dir.exists() else 0
    errors = len(list(spool_dir.glob("*.error"))) if spool_dir.exists() else 0

    fts_count = None
    fts_warning = None
    try:
        from memoryweaver.store.fts import FTS5Adapter

        fts_count = FTS5Adapter().count()
    except Exception as exc:
        fts_warning = (
            "FTS unavailable on the current data path; semantic search remains available: "
            f"{exc}"
        )

    lines = [
        "MemoryWeaver Status",
        f"  data_dir           : {os.environ.get('MEMORYWEAVER_DATA_DIR', DEFAULT_MEMORYWEAVER_DATA_DIR)}",
        f"  explicit_memories : {explicit_count} live / {explicit_total} total",
        f"  inferred_memories : {inferred_count} live / {inferred_total} total",
        f"  fts_index         : {fts_count if fts_count is not None else 'unavailable'}",
        f"  chroma_tombstones : {max((explicit_total + inferred_total) - (explicit_count + inferred_count), 0)}",
        f"  spool pending     : {pending}",
        f"  spool done        : {done}",
        f"  spool errors      : {errors}",
    ]
    chroma_live = explicit_count + inferred_count
    if fts_warning:
        lines.append(f"  [WARN] {fts_warning}")
    elif fts_count == 0 and chroma_live > 0:
        lines.append("  [WARN] FTS index empty -- run: python -m memoryweaver sync-fts")
    elif fts_count is not None and fts_count < chroma_live:
        lines.append(f"  [WARN] FTS ({fts_count}) < Chroma live ({chroma_live}) -- run: python -m memoryweaver sync-fts")

    try:
        from memoryweaver.producer import read_capture_status
        cap = read_capture_status()
        if cap:
            lines.append("  capture (per harness):")
            for h in sorted(cap):
                r = cap[h] if isinstance(cap[h], dict) else {}
                lines.append(f"    {h:<12}: last={r.get('last_ts','?')} count={r.get('count',0)}")
    except Exception:
        pass

    return "\n".join(lines)


@app.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="memory_recall",
            description="Hybrid BM25+semantic search across accumulated memories (decisions, inferred patterns, project knowledge). Use this to recall past context before starting new work.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "project": {"type": "string", "description": "Filter by project name"},
                    "top_k": {"type": "integer", "default": 10, "description": "Max results"},
                    "include_inferred": {"type": "boolean", "default": False, "description": "Include inferred memories"},
                    "search": {"type": "string", "enum": ["hybrid", "semantic"], "default": "hybrid"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="memory_browse",
            description="Browse memories by metadata: wing, room, drawer, tag, or project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "wing": {"type": "string"},
                    "room": {"type": "string"},
                    "drawer": {"type": "string"},
                    "tag": {"type": "string"},
                    "project": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="memory_status",
            description="Show memoryweaver ledger status: counts of explicit/inferred memories and spools processed.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="memory_stub_list",
            description="Cheap two-tier retrieval: returns compact ~50-100 token stubs (identity_key, confidence, tags, gist) for the most relevant memories. Use FIRST to triage cheaply, then memory_fetch by identity_key for full detail. Token-frugal vs memory_recall (which returns full text).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {"type": "integer", "default": 10},
                    "project": {"type": "string", "description": "Filter by project name"},
                    "include_inferred": {"type": "boolean", "default": False},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="memory_fetch",
            description="Fetch full memory detail by identity_key(s) returned from memory_stub_list. The second tier of the cheap-stub -> full-detail split.",
            inputSchema={
                "type": "object",
                "properties": {
                    "identity_keys": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["identity_keys"],
            },
        ),
        types.Tool(
            name="memory_derive",
            description="Author a DERIVED memory that cites its source observation identity_keys (from memory_stub_list/memory_recall). Non-generative: YOU supply the conclusion text + the source ids it rests on. Cited ids that don't resolve cause the record to be quarantined (still stored). Use this to record a conclusion grounded in specific prior memories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The derived conclusion"},
                    "cites": {"type": "array", "items": {"type": "string"}, "description": "Source identity_keys this conclusion is grounded in"},
                    "predicate": {"type": "string", "enum": ["cites", "supersedes", "refines"], "default": "cites", "description": "Typed edge from this conclusion to its sources (Phase 2.5-D). Defaults to 'cites'."},
                    "project": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "cites"],
            },
        ),
        types.Tool(
            name="memory_synthesize",
            description="Run non-generative derivation strategies: cluster existing memories by tag and/or thread and create derived INDEX memories citing each cluster. Re-runnable (supersedes stale rollups). No LLM — index text is a deterministic list of member gists.",
            inputSchema={
                "type": "object",
                "properties": {
                    "strategies": {"type": "array", "items": {"type": "string", "enum": ["tag", "thread"]}, "description": "Defaults to both."},
                },
            },
        ),
        types.Tool(
            name="memory_revoke",
            description="REVOKE a memory by identity_key — mark it deleted-because-WRONG (not merely superseded) and flag every memory that directly cites it as quarantined (non-cascading). Use when a source memory was incorrect and conclusions built on it should no longer be trusted. Distinct from supersession (a newer version replacing an older one).",
            inputSchema={
                "type": "object",
                "properties": {
                    "identity_key": {"type": "string", "description": "identity_key of the memory to revoke"},
                },
                "required": ["identity_key"],
            },
        ),
        types.Tool(
            name="memory_harvest_links",
            description="Harvest the links a human already authored — Obsidian [[wikilinks]] (predicate 'refines') and 'supersedes X' references (predicate 'supersedes') in a piece of text — into ONE derived memory with TYPED candidate cites. Targets resolve deterministically to existing memories (unique match only); unresolved targets keep the record QUARANTINED (never canonical until they resolve). Bounded + non-generative. Use to wire DECISIONS entries / notes into the citation graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Authored text containing [[wikilinks]] and/or 'supersedes ...' references"},
                    "derived_text": {"type": "string", "description": "Conclusion text for the harvested record (defaults to the first line of `text`)"},
                    "project": {"type": "string"},
                    "thread": {"type": "string"},
                    "source_uri": {"type": "string"},
                },
                "required": ["text"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    from memoryweaver.commands import browse as browse_mod, recall as recall_mod

    try:
        if name == "memory_recall":
            argv = [arguments["query"]]
            if arguments.get("project"):
                argv += ["--project", arguments["project"]]
            if arguments.get("top_k"):
                argv += ["--top-k", str(arguments["top_k"])]
            if arguments.get("include_inferred"):
                argv.append("--include-inferred")
            if arguments.get("search"):
                argv += ["--search", arguments["search"]]
            result = recall_mod.run(argv)
            return [types.TextContent(type="text", text=result)]

        if name == "memory_browse":
            argv = []
            for key in ["wing", "room", "drawer", "tag", "project"]:
                if arguments.get(key):
                    argv += [f"--{key}", arguments[key]]
            result = browse_mod.run(argv)
            return [types.TextContent(type="text", text=result)]

        if name == "memory_status":
            return [types.TextContent(type="text", text=_status_text())]

        if name == "memory_stub_list":
            import json as _json
            from memoryweaver.store.chroma import ChromaDBAdapter
            from memoryweaver.stub import project as _project
            store = ChromaDBAdapter()
            results = store.query_hybrid(
                text=arguments["query"],
                top_k=arguments.get("top_k", 10),
                project=arguments.get("project"),
                include_inferred=arguments.get("include_inferred", False),
            )
            stubs = [_project(r).to_dict() for r in results]
            return [types.TextContent(type="text", text=_json.dumps(stubs, ensure_ascii=False, indent=1))]

        if name == "memory_fetch":
            import json as _json
            from memoryweaver.store.chroma import ChromaDBAdapter
            store = ChromaDBAdapter()
            out = []
            for ik in (arguments.get("identity_keys") or []):
                rec = store.fetch_by_id(ik)
                if rec is None:
                    out.append({"identity_key": ik, "found": False})
                else:
                    doc = rec.to_chroma_doc()
                    out.append({
                        "identity_key": ik,
                        "found": True,
                        "text": rec.text,
                        "metadata": doc["metadata"],
                    })
            return [types.TextContent(type="text", text=_json.dumps(out, ensure_ascii=False, indent=1))]

        if name == "memory_derive":
            import json as _json
            from memoryweaver.derive import make_derived
            res = make_derived(
                text=arguments["text"],
                cites=arguments.get("cites") or [],
                predicate=arguments.get("predicate") or "cites",
                project=arguments.get("project"),
                tags=arguments.get("tags") or [],
            )
            return [types.TextContent(type="text", text=_json.dumps(res, ensure_ascii=False, indent=1))]

        if name == "memory_synthesize":
            import json as _json
            from memoryweaver.derive import synthesize
            res = synthesize(strategies=arguments.get("strategies") or None)
            return [types.TextContent(type="text", text=_json.dumps(res, ensure_ascii=False, indent=1))]

        if name == "memory_revoke":
            import json as _json
            from memoryweaver.provenance import revoke
            from memoryweaver.store.chroma import ChromaDBAdapter
            res = revoke(ChromaDBAdapter(), arguments["identity_key"])
            return [types.TextContent(type="text", text=_json.dumps(res, ensure_ascii=False, indent=1))]

        if name == "memory_harvest_links":
            import json as _json
            from memoryweaver.links import harvest
            res = harvest(
                text=arguments["text"],
                derived_text=arguments.get("derived_text"),
                project=arguments.get("project"),
                thread=arguments.get("thread"),
                source_uri=arguments.get("source_uri"),
            )
            return [types.TextContent(type="text", text=_json.dumps(res, ensure_ascii=False, indent=1))]

    except Exception as exc:
        import traceback
        return [types.TextContent(type="text", text=f"Error: {exc}\n{traceback.format_exc()}")]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
