"""
Derivation workflow (Phase 2.5) — the single seam for creating DERIVED memories
that cite their source observations. NON-GENERATIVE (no LLM, ever).

Three sources, ONE seam (`make_derived`):
  - manual        : memory_derive tool / `derive` CLI (agent supplies text + cites)
  - rollup (tag)  : synthesize(strategies=["tag"])    — cluster by project+tag
  - links (thread): synthesize(strategies=["thread"]) — cluster by thread

Every derivation routes through make_derived -> validate_cites (resolve-or-
quarantine, Phase 2) -> upsert, so one provenance path governs all of them.

See .claude/memoryweaver/stub-tier-plan/PLAN.md.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

from .models import MemoryRecord
from .provenance import validate_cites

_DERIVE_TAG_PREFIX = "derivation:"
_ROLLUP_SCHEME = "derive://rollup"
_MIN_CLUSTER = 3
_MAX_MEMBERS_IN_TEXT = 12

# Phase 2.5-D #3: cite predicate is a CLOSED enum. Anything else collapses to
# "cites" (no open-vocabulary drift; the typed edge stays machine-readable).
_PREDICATES = ("cites", "supersedes", "refines")
_DEFAULT_PREDICATE = "cites"


def _norm_predicate(p) -> str:
    return p if p in _PREDICATES else _DEFAULT_PREDICATE


def _store_default():
    from .store.chroma import ChromaDBAdapter
    return ChromaDBAdapter()


def _decay_policy_version() -> Optional[str]:
    try:
        from .decay import DecayScorer
        s = DecayScorer()
        return getattr(s, "policy_version_for_record", lambda: s.policy_version)()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# THE SEAM
# ---------------------------------------------------------------------------

def make_derived(
    text: str,
    cites: List[str],
    *,
    project: Optional[str] = None,
    thread: Optional[str] = None,
    tags: Optional[List[str]] = None,
    category: str = "derived",
    source_uri: Optional[str] = None,
    derivation_kind: str = "manual",
    predicate: str = _DEFAULT_PREDICATE,
    cite_predicates: Optional[dict] = None,
    store=None,
) -> dict:
    """
    Create + persist ONE derived (confidence=inferred) memory citing `cites`
    (source identity_keys). Validated by Phase 2 (resolve-or-quarantine).

    Phase 2.5-D additions:
      - D1: each resolvable cite captures the source's content_hash at cite-time
        (stored in cite_meta), so drift/supersession is detectable later.
      - #3: each cite carries a typed predicate (cites|supersedes|refines);
        `predicate` is the default for all cites, `cite_predicates` overrides per id.
      - #5: acyclicity guard — a record may never cite itself, nor a source that
        already cites it (back-edge); `generation` = 1 + max(cited generations)
        gives a monotonic strictly-prior order (NOT wall-clock).

    Returns {identity_key, provenance_status, cites_total, cites_resolved,
             generation, kind}.
    """
    store = store or _store_default()
    default_pred = _norm_predicate(predicate)
    cite_predicates = cite_predicates or {}
    raw_cites = [c for c in (cites or []) if c]

    all_tags = list(tags or [])
    kind_tag = f"{_DERIVE_TAG_PREFIX}{derivation_kind}"
    if kind_tag not in all_tags:
        all_tags.append(kind_tag)

    # Build the record first (its identity_key depends on text/uri/type/scope,
    # NOT on cites) so the acyclicity guard can reject self-citation.
    rec = MemoryRecord(
        text=text,
        source_uri=source_uri or f"derive://{derivation_kind}",
        memory_type="inferred",
        confidence="inferred",
        timestamp=datetime.now(tz=timezone.utc),
        project=project,
        thread=thread,
        tags=all_tags,
        category=category,
        cites=[],
    )
    self_id = rec.identity_key

    kept: List[str] = []
    cite_meta: dict = {}
    resolved = 0
    max_gen = -1
    for c in raw_cites:
        if c == self_id:
            continue  # #5: no self-citation
        pred = _norm_predicate(cite_predicates.get(c, default_pred))
        src = None
        try:
            src = store.fetch_by_id(c)
        except Exception:
            src = None
        if src is not None:
            # #5: reject a back-edge (the cited source already cites us) — the
            # only realisable cycle at creation time.
            src_back = (getattr(src, "cite_meta", None) or {})
            src_cites = (getattr(src, "cites", None) or [])
            if self_id in src_back or self_id in src_cites:
                continue
            resolved += 1
            cite_meta[c] = {"content_hash": src.content_hash, "predicate": pred}  # D1 + #3
            try:
                max_gen = max(max_gen, int(getattr(src, "generation", 0) or 0))
            except Exception:
                pass
        else:
            # Unresolved now — still recorded; Phase-2 validation will quarantine.
            cite_meta[c] = {"content_hash": None, "predicate": pred}
        kept.append(c)

    rec.cites = kept
    rec.cite_meta = cite_meta
    rec.generation = max_gen + 1  # strictly-prior, monotonic, content-derived

    pv = _decay_policy_version()
    if pv:
        rec.decay_policy_version = pv
    try:
        rec.provenance_status = validate_cites(rec, store)
    except Exception:
        rec.provenance_status = "valid"  # fail-open
    store.upsert(rec)

    return {
        "identity_key": rec.identity_key,
        "provenance_status": rec.provenance_status,
        "cites_total": len(kept),
        "cites_resolved": resolved,
        "generation": rec.generation,
        "kind": derivation_kind,
    }


# ---------------------------------------------------------------------------
# Automatic sources (rollups / links) — all funnel into make_derived
# ---------------------------------------------------------------------------

def _is_derivation(rec: MemoryRecord) -> bool:
    return any((t or "").startswith(_DERIVE_TAG_PREFIX) for t in (rec.tags or []))


def _all_source_records(store) -> List[MemoryRecord]:
    """Non-derived, non-superseded records — the citable observations."""
    recs = store.query_browse(confidence="all")
    return [r for r in recs if not _is_derivation(r) and not r.superseded]


def _gist1(text: str, n: int = 90) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= n else flat[:n].rsplit(" ", 1)[0] + "…"


def _supersede_prior_rollups(store, source_uri: str) -> None:
    """Tombstone any prior live rollup with this stable source_uri (idempotent re-run)."""
    try:
        for r in store.query_browse(confidence="all"):
            if r.source_uri == source_uri and not r.superseded:
                store.delete_soft(r.identity_key)
    except Exception:
        pass


def _make_rollup(store, strategy: str, cluster_key: str, members: List[MemoryRecord],
                 project: Optional[str], thread: Optional[str], summary: dict) -> None:
    if len(members) < summary["min_cluster"]:
        return
    cites = [m.identity_key for m in members]
    shown = members[:_MAX_MEMBERS_IN_TEXT]
    bullets = "\n".join(f"- {_gist1(m.text)}" for m in shown)
    more = f"\n- (+{len(members) - len(shown)} more)" if len(members) > len(shown) else ""
    text = f"Index ({strategy}={cluster_key}): {len(members)} memories\n{bullets}{more}"
    source_uri = f"{_ROLLUP_SCHEME}/{strategy}/{cluster_key}"
    _supersede_prior_rollups(store, source_uri)
    res = make_derived(
        text=text, cites=cites, project=project, thread=thread,
        tags=[f"rollup:{strategy}"], category="rollup",
        source_uri=source_uri, derivation_kind=f"rollup-{strategy}", store=store,
    )
    summary["rollups_created"] += 1
    summary["records_cited"] += res["cites_total"]


def synthesize(store=None, strategies: Optional[List[str]] = None,
               min_cluster: int = _MIN_CLUSTER) -> dict:
    """
    Run non-generative derivation strategies over existing memories. Rollup text
    is a DETERMINISTIC index of member gists (no model). Re-runnable (supersedes
    stale rollups for the same cluster).

    strategies: subset of {"tag", "thread"}. Default both.
    """
    store = store or _store_default()
    strategies = strategies or ["tag", "thread"]
    summary = {"strategies": strategies, "rollups_created": 0, "records_cited": 0,
               "clusters_seen": 0, "min_cluster": min_cluster}

    sources = _all_source_records(store)

    if "tag" in strategies:
        clusters = defaultdict(list)
        for r in sources:
            for tag in (r.tags or []):
                clusters[(r.project or "", tag)].append(r)
        for (proj, tag), members in clusters.items():
            summary["clusters_seen"] += 1
            _make_rollup(store, "tag", f"{proj}|{tag}", members, proj or None, None, summary)

    if "thread" in strategies:
        clusters = defaultdict(list)
        for r in sources:
            if r.thread:
                clusters[r.thread].append(r)
        for thread, members in clusters.items():
            summary["clusters_seen"] += 1
            _make_rollup(store, "thread", thread, members, None, thread, summary)

    summary.pop("min_cluster", None)
    return summary
