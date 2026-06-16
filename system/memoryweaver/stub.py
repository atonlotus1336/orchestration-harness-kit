"""
Stub projection — cheap, deterministic, NON-generative memory stubs.

A Stub is a compact (~50-100 token) view over a MemoryRecord or a query-result
dict, built by pure string operations only (NO LLM, NO summarization model).

Two-tier retrieval (stub-tier plan, Phase 1):
  memory_stub_list -> [Stub]  (cheap triage)
  memory_fetch(identity_key)  -> full MemoryRecord detail

See .claude/memoryweaver/stub-tier-plan/PLAN.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Optional

from .models import MemoryRecord

# Bump to recompute gists on next read. Gists are PROJECTED, not stored, so a
# version bump needs no migration (Codex ARCH/PLAN: zero-drift projection).
GIST_VERSION = "1"

# Deterministic gist cap. ~100 tokens is roughly ~400 chars; we cap on chars
# (not tokens) so the projection stays fully deterministic and model-free.
_GIST_MAX_CHARS = 220


@dataclass
class Stub:
    identity_key: str
    confidence: str          # "explicit" | "inferred"  (== observation | derived axis)
    memory_type: str         # "session" | "decision" | "inferred"
    category: str            # "decision" | "learning" | "preference" | "fact" | ""
    project: str
    tags: list[str]
    gist: str
    gist_version: str = GIST_VERSION
    score: Optional[float] = None
    provenance_status: str = "valid"   # "valid" | "quarantined" (Phase 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_clause(text: str, max_chars: int = _GIST_MAX_CHARS) -> str:
    """Deterministic first-clause/sentence slice of `text`. Pure, no model."""
    if not text:
        return ""
    flat = " ".join(text.split())  # collapse whitespace deterministically
    if len(flat) <= max_chars:
        return flat
    window = flat[:max_chars]
    # Prefer the last sentence/clause boundary in the back half of the window.
    for sep in (". ", "; ", " — ", ", "):
        idx = window.rfind(sep)
        if idx >= max_chars // 2:
            return window[: idx + 1].rstrip()
    # Fall back to the last word boundary.
    idx = window.rfind(" ")
    return (window[:idx] if idx > 0 else window).rstrip() + "…"


def _tags_of(meta: dict) -> list[str]:
    raw = meta.get("tags", "[]")
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return [t for t in str(raw).split(",") if t]


def _as_fields(item: Any) -> tuple[str, dict, str, Optional[float]]:
    """Normalize a MemoryRecord OR a query-result dict to (identity_key, metadata, text, score)."""
    if isinstance(item, MemoryRecord):
        doc = item.to_chroma_doc()
        return doc["id"], doc["metadata"], item.text, None
    if isinstance(item, dict):
        # query_semantic / query_hybrid result shape: {id, document, metadata, score, ...}
        meta = item.get("metadata") or {}
        ident = item.get("id") or meta.get("content_hash") or ""
        text = item.get("document") or ""
        score = item.get("rrf_score", item.get("combined_score", item.get("score")))
        return ident, meta, text, score
    raise TypeError(
        f"project() expects MemoryRecord or query-result dict, got {type(item).__name__}"
    )


def project(item: Any) -> Stub:
    """
    Build a deterministic Stub from a MemoryRecord or a query-result dict.

    Non-generative: the gist is a pure slice of the stored text plus metadata.
    Tolerant of malformed/legacy records (PLAN-007): missing fields -> safe defaults,
    never raises on a record shape.
    """
    ident, meta, text, score = _as_fields(item)
    return Stub(
        identity_key=ident or "",
        confidence=(meta.get("confidence") or "unknown"),
        memory_type=(meta.get("memory_type") or "unknown"),
        category=(meta.get("category") or ""),
        project=(meta.get("project") or ""),
        tags=_tags_of(meta),
        gist=_first_clause(text),
        gist_version=GIST_VERSION,
        score=round(float(score), 4) if isinstance(score, (int, float)) else None,
        provenance_status=(meta.get("provenance_status") or "valid"),
    )
