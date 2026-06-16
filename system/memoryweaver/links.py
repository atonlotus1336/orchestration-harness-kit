"""
Human-link candidate harvest (Phase 2.7) — the governed version of the council's
Team-C remix "R" (eliminated as raw harvest in Phase 1, re-admitted as a *bounded,
typed, never-canonical* suggestion layer).

Extracts the links a human ALREADY authors — Obsidian ``[[wikilinks]]`` and
``supersedes X`` references in DECISIONS-style text — as TYPED, UNVALIDATED
candidate cites:

  - ``[[wikilink]]``        -> predicate ``refines``    (associative reference)
  - ``supersedes [[X]]``    -> predicate ``supersedes`` (lineage claim)

Candidates resolve DETERMINISTICALLY against existing memories (unique exact or
unique long-prefix title match — never a fuzzy guess). Everything then routes
through the Phase-2.5-D ``make_derived`` hash-gate:

  - resolved targets  -> real cites, content_hash-pinned, typed, acyclicity-guarded
  - unresolved targets -> recorded as ``unresolved://<target>`` cites that never
    resolve, so the harvested record stays QUARANTINED (never canonical until
    every target resolves+hashes — the structuralist's open-world veto is honored)

Bounded: at most ``_MAX_CANDIDATES`` per harvest; excess is dropped and REPORTED
(no silent truncation). NON-GENERATIVE — no LLM anywhere.

See .claude/memoryweaver/stub-tier-plan/PLAN.md.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import List, Optional

from .derive import make_derived, _store_default

# [[Target]] or [[Target|alias]] — capture Target only.
_WIKILINK = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")
# "supersedes <object>" — capture ONLY the immediate object: a single [[wikilink]]
# or the text up to the first clause terminator. Must not swallow later links.
_SUPERSEDES = re.compile(r"\bsupersed(?:es|ing)\b[:\s]+(\[\[[^\]]+\]\]|[^.;,\n]+)", re.IGNORECASE)

# Bounded quarantine: cap candidates per harvest so a link-heavy note cannot
# flood the store. Excess is dropped and reported, never silently truncated.
_MAX_CANDIDATES = 24
_UNRESOLVED_SCHEME = "unresolved://"
_MIN_PREFIX = 4  # don't prefix-match on targets shorter than this


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return (text or "").strip()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_candidates(text: str) -> List[dict]:
    """
    Return [{"target": str, "predicate": "refines"|"supersedes"}] for a piece of
    authored text. A target captured as `supersedes` is not re-emitted as `refines`.
    """
    out: List[dict] = []
    seen = set()

    for m in _SUPERSEDES.finditer(text or ""):
        tail = m.group(1)
        wl = _WIKILINK.findall(tail)
        # Prefer explicit [[targets]]; else take the clause up to a terminator.
        targets = wl if wl else [re.split(r"[.;,\n]", tail, 1)[0]]
        for t in targets:
            n = _norm(t)
            if n and ("supersedes", n) not in seen:
                seen.add(("supersedes", n))
                out.append({"target": t.strip(), "predicate": "supersedes"})

    for m in _WIKILINK.finditer(text or ""):
        t = m.group(1)
        n = _norm(t)
        if not n or ("supersedes", n) in seen or ("refines", n) in seen:
            continue
        seen.add(("refines", n))
        out.append({"target": t.strip(), "predicate": "refines"})

    return out


# ---------------------------------------------------------------------------
# Deterministic resolution (no fuzzy guessing)
# ---------------------------------------------------------------------------

def _title_tokens(rec) -> set:
    """Normalized tokens a wikilink could name: source basename + decision name."""
    toks = set()
    uri = getattr(rec, "source_uri", "") or ""
    base = uri.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if base:
        toks.add(_norm(base))
    first = _first_line(getattr(rec, "text", "") or "")
    if first:
        toks.add(_norm(first)[:80])
        # DECISIONS format: "[date] — Name: rationale" -> grab the Name.
        if "—" in first:
            name = first.split("—", 1)[1].split(":", 1)[0]
            if _norm(name):
                toks.add(_norm(name))
    toks.discard("")
    return toks


def _build_index(store) -> dict:
    idx = defaultdict(set)
    try:
        recs = store.query_browse(confidence="all")
    except Exception:
        recs = []
    for r in recs:
        for tok in _title_tokens(r):
            idx[tok].add(r.identity_key)
    return idx


def resolve_candidate(target: str, idx: dict) -> Optional[str]:
    """Resolve a link target to a UNIQUE identity_key, else None (-> quarantine)."""
    n = _norm(target)
    if not n:
        return None
    exact = idx.get(n) or set()
    if len(exact) == 1:
        return next(iter(exact))
    if len(exact) > 1:
        return None  # ambiguous — refuse to guess
    if len(n) >= _MIN_PREFIX:
        hits = set()
        for tok, ids in idx.items():
            if tok.startswith(n):
                hits |= ids
        if len(hits) == 1:
            return next(iter(hits))
    return None


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------

def harvest(
    text: str,
    *,
    project: Optional[str] = None,
    thread: Optional[str] = None,
    source_uri: Optional[str] = None,
    derived_text: Optional[str] = None,
    store=None,
    max_candidates: int = _MAX_CANDIDATES,
) -> dict:
    """
    Harvest the links in `text` into ONE derived record (confidence=inferred),
    routed through the make_derived hash-gate. Unresolved targets keep the record
    quarantined. Returns a summary including resolved/unresolved counts + any
    dropped (over-cap) targets.
    """
    store = store or _store_default()
    candidates = extract_candidates(text)
    dropped: List[str] = []
    if len(candidates) > max_candidates:
        dropped = [c["target"] for c in candidates[max_candidates:]]
        candidates = candidates[:max_candidates]

    if not candidates:
        return {
            "identity_key": None, "provenance_status": None,
            "candidates": 0, "resolved": 0, "unresolved": 0,
            "dropped": len(dropped), "dropped_targets": dropped,
        }

    idx = _build_index(store)
    cites: List[str] = []
    cite_predicates: dict = {}
    resolved = 0
    unresolved_targets: List[str] = []
    for c in candidates:
        ik = resolve_candidate(c["target"], idx)
        if ik:
            cites.append(ik)
            cite_predicates[ik] = c["predicate"]
            resolved += 1
        else:
            # Synthetic, never-resolving cite -> record stays quarantined.
            synth = f"{_UNRESOLVED_SCHEME}{_norm(c['target'])}"
            cites.append(synth)
            cite_predicates[synth] = c["predicate"]
            unresolved_targets.append(c["target"])

    res = make_derived(
        text=derived_text or _first_line(text),
        cites=cites,
        project=project,
        thread=thread,
        tags=["harvest:links"],
        category="link-harvest",
        source_uri=source_uri,
        derivation_kind="link-harvest",
        cite_predicates=cite_predicates,
        store=store,
    )
    res.update({
        "candidates": len(candidates),
        "resolved": resolved,
        "unresolved": len(unresolved_targets),
        "unresolved_targets": unresolved_targets,
        "dropped": len(dropped),
        "dropped_targets": dropped,
    })
    return res
