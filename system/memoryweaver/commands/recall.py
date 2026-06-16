"""
/memory-recall command — hybrid BM25+semantic search against memories.

Usage (Claude Code slash command):
  /memory-recall <query>
  /memory-recall <query> --project <name>
  /memory-recall <query> --thread <name>
  /memory-recall <query> --decay off
  /memory-recall <query> --include-inferred
  /memory-recall <query> --top-k 20
  /memory-recall <query> --search semantic      (skip BM25, semantic only)
  /memory-recall <query> --search hybrid        (default: BM25 + semantic via RRF)

Defaults to explicit_memories only (fixes E-002).
Default search mode: hybrid (BM25 + semantic RRF).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from ..store.chroma import ChromaDBAdapter
from ..decay import DecayScorer


def run(argv: list[str]) -> str:
    """Entry point — returns markdown-formatted results string."""
    parser = argparse.ArgumentParser(prog="memory-recall", add_help=False)
    parser.add_argument("query", nargs="+")
    parser.add_argument("--project", default=None)
    parser.add_argument("--thread", default=None)
    parser.add_argument("--decay", choices=["on", "off"], default="on")
    parser.add_argument("--include-inferred", action="store_true")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--search",
        choices=["hybrid", "semantic"],
        default="hybrid",
        help="Search mode: hybrid=BM25+semantic RRF (default), semantic=vector only",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return "_Usage: /memory-recall <query> [--project name] [--thread name] [--decay on|off] [--include-inferred] [--top-k N]_"

    query = " ".join(args.query)
    store = ChromaDBAdapter()
    scorer = DecayScorer()
    search_mode = args.search  # "hybrid" | "semantic"

    if search_mode == "hybrid":
        results = store.query_hybrid(
            text=query,
            top_k=args.top_k,
            project=args.project,
            thread=args.thread,
            include_inferred=args.include_inferred,
        )
    else:
        results = store.query_semantic(
            text=query,
            top_k=args.top_k,
            project=args.project,
            thread=args.thread,
            include_inferred=args.include_inferred,
        )

    if not results:
        return f"_No memories found for: **{query}**_"

    # For hybrid results, decay scorer uses item["score"] as the relevance base.
    # Substitute rrf_score so decay multiplies the fused rank rather than raw cosine.
    if search_mode == "hybrid":
        for r in results:
            if "rrf_score" in r:
                r["score"] = r["rrf_score"]

    if args.decay == "on":
        results = scorer.score(results)
        sort_key = "combined_score"
    else:
        sort_key = "score"
        results.sort(key=lambda x: x.get(sort_key, 0), reverse=True)

    return _format_results(results, query, sort_key, args.decay == "on", search_mode)


def _format_results(
    results: list, query: str, sort_key: str, decay_on: bool, search_mode: str = "hybrid"
) -> str:
    lines = [f"## Memory Recall: `{query}`\n"]
    lines.append(
        f"_Found {len(results)} result(s). "
        f"Search: {search_mode}. Decay: {'on' if decay_on else 'off'}_\n"
    )

    for i, r in enumerate(results, 1):
        meta = r.get("metadata") or {}
        confidence = meta.get("confidence", "?")
        source = meta.get("source_uri", "?")
        project = meta.get("project", "")
        score = r.get(sort_key, 0)
        decay_score = r.get("decay_score", "")
        policy_ver = r.get("decay_policy_version", "")
        text = r.get("document", "")

        # Truncate long text in preview
        preview = text[:300] + "…" if len(text) > 300 else text

        lines.append(f"### {i}. Score: {score:.3f}")
        meta_parts = [f"confidence={confidence}"]
        if project:
            meta_parts.append(f"project={project}")
        if decay_score:
            meta_parts.append(f"decay={decay_score} (policy v{policy_ver})")
        meta_parts.append(f"source={Path(source).name if source != '?' else source}")
        lines.append(f"_{', '.join(meta_parts)}_\n")
        lines.append(f"{preview}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry (for testing)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(run(sys.argv[1:]))
