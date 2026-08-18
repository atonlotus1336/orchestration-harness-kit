"""
rrf_merge — Reciprocal Rank Fusion for hybrid BM25 + semantic search.

Standard RRF formula (Cormack et al. 2009):
    score(d) = Σ  1 / (k + rank(d, list_i))

k=60 is the canonical constant — it dampens the impact of very high-ranked
documents and prevents any single list from dominating. Empirically optimal
across many IR benchmarks.

Usage:
    from memoryweaver.store.hybrid import rrf_merge

    semantic = store.query_semantic(query, top_k=20)
    bm25     = fts.query_bm25(query, top_k=20)
    merged   = rrf_merge(semantic, bm25, top_k=10)

Each input list item must have an "id" key.
Output items carry all keys from the source dicts plus "rrf_score".
Items present in only one list are included (union, not intersection).
"""

from __future__ import annotations

from typing import List


def rrf_merge(
    semantic: List[dict],
    bm25: List[dict],
    k: int = 60,
    top_k: int = 10,
) -> List[dict]:
    """
    Merge two ranked result lists via Reciprocal Rank Fusion.

    Args:
        semantic:  Results from query_semantic() — each dict must have "id".
        bm25:      Results from query_bm25()     — each dict must have "id".
        k:         RRF constant (default 60). Higher = less weight to top ranks.
        top_k:     Maximum results to return.

    Returns:
        Merged list sorted by rrf_score descending, capped at top_k.
        Each item is the source dict enriched with {"rrf_score": float}.
    """
    rrf_scores: dict[str, float] = {}

    for rank, item in enumerate(semantic):
        id_ = item.get("id", "")
        if id_:
            rrf_scores[id_] = rrf_scores.get(id_, 0.0) + 1.0 / (k + rank + 1)

    for rank, item in enumerate(bm25):
        id_ = item.get("id", "")
        if id_:
            rrf_scores[id_] = rrf_scores.get(id_, 0.0) + 1.0 / (k + rank + 1)

    ranked_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[
        :top_k
    ]

    # Build lookup from both lists; semantic takes precedence for metadata
    # (it carries the full ChromaDB document + metadata dict).
    lookup: dict[str, dict] = {}
    for item in reversed(bm25):        # load bm25 first (lower priority)
        if item.get("id"):
            lookup[item["id"]] = item
    for item in reversed(semantic):    # semantic overwrites (higher priority)
        if item.get("id"):
            lookup[item["id"]] = item

    results = []
    for id_ in ranked_ids:
        if id_ in lookup:
            entry = dict(lookup[id_])
            entry["rrf_score"] = round(rrf_scores[id_], 6)
            results.append(entry)

    return results
