"""
Provenance validation — ingest-time citation checking with QUARANTINE (never reject).

Phase 2 of the stub-tier plan. Rules (Codex-hardened ARCH-005/013, PLAN-001/002):
  - Capture is fail-open: validation NEVER rejects/drops a record. Invalid
    provenance is recorded as provenance_status="quarantined" (still stored).
  - confidence != "inferred"  -> always "valid" (observations need no citations).
  - inferred with empty cites  -> "valid" (legacy/un-cited inferred is not
    mass-quarantined; strict "must-cite" enforcement is a future policy toggle).
  - inferred with cites        -> every cite must resolve in the committed store
    OR the current ingest batch; any unresolved cite -> "quarantined".
  - revalidate_quarantined() re-checks quarantined records after a batch and
    flips them to "valid" once their cited sources have landed.

See .claude/memoryweaver/stub-tier-plan/PLAN.md.
"""

from __future__ import annotations

import json
from typing import Iterable, Optional, Set


def _cite_resolves(store, key: str, batch_keys: Set[str]) -> bool:
    """
    A cite resolves iff its live head (following supersession) exists and is NOT
    revoked (Phase 2.6). batch siblings resolve unconditionally (not stored yet).

      - missing entirely          -> unresolved
      - superseded WITH successor  -> resolves (resolve_live walks to the successor)
      - superseded successorless   -> resolves only if not revoked
      - REVOKED                    -> unresolved (the "this was wrong" case)
    """
    if key in batch_keys:
        return True
    try:
        head = resolve_live(store, key)
    except Exception:
        return False  # store error on a single lookup -> unresolved, not fatal
    if head is None:
        return False
    return not getattr(head, "revoked", False)


def validate_cites(record, store, batch_keys: Optional[Iterable[str]] = None) -> str:
    """
    Return "valid" or "quarantined" for a record about to be ingested.

    `store` must expose fetch_by_id(identity_key) -> record | None.
    `batch_keys` are identity_keys present in the same ingest batch (so a record
    citing a sibling ingested in the same batch resolves regardless of order).

    Phase 2.6: a cite to a REVOKED source no longer resolves -> the dependent is
    quarantined (revocation is honored here, not only at delete-time).
    """
    keys: Set[str] = set(batch_keys or ())

    if getattr(record, "confidence", None) != "inferred":
        return "valid"

    cites = getattr(record, "cites", None) or []
    if not cites:
        return "valid"

    for key in cites:
        if not _cite_resolves(store, key, keys):
            return "quarantined"  # at least one citation unresolved or revoked

    return "valid"


def resolve_live(store, key: str, max_hops: int = 16):
    """
    Follow superseded_by forward-pointers from `key` to the current LIVE head
    record (Phase 2.5-D — D2 supersession-chain traversal).

    Returns the live MemoryRecord (the first one not marked superseded), or the
    last reachable record if the chain dead-ends, or None if `key` resolves to
    nothing. Bounded by `max_hops` and cycle-guarded so a malformed
    superseded_by loop can never hang.

    A cite stays "valid" the moment its source resolves at all (soft-deleted
    rows are kept, so fetch_by_id still finds a superseded source — that is why
    superseding a cited source never retro-quarantines a derivation). resolve_live
    is the *freshness* layer on top: genealogy/recall can walk a cite to whatever
    record currently supersedes the one that was originally cited.
    """
    seen: Set[str] = set()
    cur = key
    last = None
    for _ in range(max_hops):
        try:
            rec = store.fetch_by_id(cur)
        except Exception:
            return last
        if rec is None:
            return last
        last = rec
        nxt = getattr(rec, "superseded_by", None)
        if not getattr(rec, "superseded", False) or not nxt:
            return rec
        if nxt in seen:
            return rec  # cycle guard — return the last record before the loop
        seen.add(cur)
        cur = nxt
    return last


def revalidate_quarantined(store, newly_committed_keys: Optional[Iterable[str]] = None) -> int:
    """
    Re-check quarantined records and flip those whose citations now resolve.

    Returns the number of records flipped quarantined -> valid. Best-effort and
    fail-open: any error on a single record is skipped, never raised.

    `newly_committed_keys` is an optional hint (the keys just ingested); the
    implementation re-checks all quarantined records against the live store,
    which is correct and bounded because the quarantined set is small.
    """
    flipped = 0
    try:
        store._get_client()  # ChromaDBAdapter lazy init
    except Exception:
        return 0

    for col in (getattr(store, "_explicit", None), getattr(store, "_inferred", None)):
        if col is None:
            continue
        try:
            total = col.count()
            res = col.get(
                where={"provenance_status": {"$eq": "quarantined"}},
                include=["documents", "metadatas"],
                limit=max(total, 1),
            )
        except Exception:
            continue

        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []

        for i, ik in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            try:
                cites = json.loads(meta.get("cites", "[]") or "[]")
            except Exception:
                cites = []
            try:
                resolves = all(_cite_resolves(store, c, set()) for c in cites) if cites else True
            except Exception:
                resolves = False
            if not resolves:
                continue
            new_meta = dict(meta)
            new_meta["provenance_status"] = "valid"
            try:
                col.update(
                    ids=[ik],
                    metadatas=[new_meta],
                    documents=[docs[i] if i < len(docs) else ""],
                )
                flipped += 1
            except Exception:
                continue

    return flipped


def revoke(store, key: str) -> dict:
    """
    Phase 2.6 revocation: mark `key` REVOKED (deleted because wrong; successorless)
    and flag its DIRECT dependents quarantined. NON-CASCADING — only records that
    cite `key` directly are re-validated; their own dependents are left untouched
    (the council's mark-only rule, no delete-storm).

    Returns {"revoked", "direct_dependents", "flagged_quarantined"}.
    """
    try:
        store.delete_soft(key, revoked=True)
    except Exception:
        pass

    direct = 0
    flagged = 0
    try:
        live = store.query_browse(confidence="all")  # excludes superseded (incl. the now-revoked key)
    except Exception:
        live = []

    for rec in live:
        if key not in (getattr(rec, "cites", None) or []):
            continue
        if getattr(rec, "revoked", False):
            continue
        direct += 1
        try:
            new_status = validate_cites(rec, store)  # now sees `key` revoked -> quarantined
        except Exception:
            continue
        if new_status != getattr(rec, "provenance_status", "valid"):
            rec.provenance_status = new_status
            try:
                store.upsert(rec)
                flagged += 1
            except Exception:
                pass

    return {"revoked": key, "direct_dependents": direct, "flagged_quarantined": flagged}
