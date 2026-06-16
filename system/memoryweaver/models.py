"""
MemoryRecord — canonical data model for all memories in the ledger.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class MemoryRecord:
    """Single memory unit stored in MemoryWeaver."""

    # Core content
    text: str
    source_uri: str                  # file path or spool envelope id
    memory_type: str                 # "session" | "decision" | "inferred"
    confidence: str                  # "explicit" | "inferred"

    # Temporal
    timestamp: datetime
    session_id: Optional[str] = None

    # Taxonomy (wing/room/drawer = MemPalace spatial hierarchy)
    wing: Optional[str] = None
    room: Optional[str] = None
    drawer: Optional[str] = None

    # Filtering
    project: Optional[str] = None
    thread: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    category: Optional[str] = None  # "decision" | "learning" | "preference" | "fact"

    # Provenance (Phase 2 — stub-tier plan): an inferred/derived record cites the
    # source identity_keys it is grounded in; provenance_status is set at ingest.
    cites: List[str] = field(default_factory=list)
    provenance_status: str = "valid"   # "valid" | "quarantined"

    # Provenance edge metadata (Phase 2.5-D): per-cite content_hash + typed
    # predicate, keyed by cited identity_key ->
    #   {"content_hash": str|None, "predicate": "cites"|"supersedes"|"refines"}.
    # Legacy records have {} and behave exactly as before (predicate -> "cites",
    # hash unknown). content_hash (D1) lets a cite detect that its source was
    # superseded/changed; predicate (#3) is a CLOSED enum naming the edge type.
    cite_meta: dict = field(default_factory=dict)
    # Logical derivation generation (Phase 2.5-D acyclicity guard #5): 0 for
    # observations/un-cited records; a derived record is 1 + max(generation of
    # its cited sources). Monotonic and content-derived — NOT a wall-clock or
    # drvfs-hot counter (clock skew on G: must never define citation order).
    generation: int = 0

    # Decay audit trail (fixes E-001)
    decay_policy_version: Optional[str] = None

    # Dedup / identity (fixes E-006)
    content_hash: Optional[str] = None       # sha256 of normalized text
    identity_key: Optional[str] = None       # hash+uri+type+scope composite

    # Tombstone / migration
    superseded: bool = False
    superseded_by: Optional[str] = None      # identity_key of newer record
    # Revocation (Phase 2.6): a record deleted because it was WRONG (not merely
    # superseded by a newer version). A revoked source is successorless and its
    # direct dependents are flagged quarantined; cites to it never resolve.
    revoked: bool = False

    def __post_init__(self) -> None:
        if self.content_hash is None:
            self.content_hash = _sha256(self.text)
        if self.identity_key is None:
            self.identity_key = _make_identity_key(
                self.content_hash,
                self.source_uri,
                self.memory_type,
                self.project or "",
            )

    def to_chroma_doc(self) -> dict:
        """Flatten to ChromaDB document format."""
        return {
            "id": self.identity_key,
            "document": self.text,
            "metadata": {
                "source_uri": self.source_uri,
                "memory_type": self.memory_type,
                "confidence": self.confidence,
                "timestamp": self.timestamp.isoformat(),
                "session_id": self.session_id or "",
                "wing": self.wing or "",
                "room": self.room or "",
                "drawer": self.drawer or "",
                "project": self.project or "",
                "thread": self.thread or "",
                "tags": json.dumps(self.tags),
                "category": self.category or "",
                "cites": json.dumps(self.cites),
                "provenance_status": self.provenance_status,
                "cite_meta": json.dumps(self.cite_meta),
                "generation": str(self.generation),
                "decay_policy_version": self.decay_policy_version or "",
                "content_hash": self.content_hash,
                "superseded": str(self.superseded).lower(),
                "superseded_by": self.superseded_by or "",
                "revoked": str(self.revoked).lower(),
            },
        }

    @classmethod
    def from_chroma_result(cls, result: dict) -> "MemoryRecord":
        """Reconstruct from ChromaDB query result dict."""
        m = result["metadata"]
        return cls(
            text=result["document"],
            source_uri=m["source_uri"],
            memory_type=m["memory_type"],
            confidence=m["confidence"],
            timestamp=datetime.fromisoformat(m["timestamp"]),
            session_id=m.get("session_id") or None,
            wing=m.get("wing") or None,
            room=m.get("room") or None,
            drawer=m.get("drawer") or None,
            project=m.get("project") or None,
            thread=m.get("thread") or None,
            tags=json.loads(m.get("tags", "[]")),
            category=m.get("category") or None,
            cites=json.loads(m.get("cites", "[]") or "[]"),
            provenance_status=m.get("provenance_status", "valid"),
            cite_meta=json.loads(m.get("cite_meta", "{}") or "{}"),
            generation=int(m.get("generation", "0") or 0),
            decay_policy_version=m.get("decay_policy_version") or None,
            content_hash=m.get("content_hash"),
            identity_key=result.get("id"),
            superseded=m.get("superseded", "false") == "true",
            superseded_by=m.get("superseded_by") or None,
            revoked=m.get("revoked", "false") == "true",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _make_identity_key(
    content_hash: str,
    source_uri: str,
    memory_type: str,
    project_scope: str,
) -> str:
    """Composite identity key (fixes E-006 — pure hash collapses distinct memories)."""
    raw = f"{content_hash}|{source_uri}|{memory_type}|{project_scope}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
