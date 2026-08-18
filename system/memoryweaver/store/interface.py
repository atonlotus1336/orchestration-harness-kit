"""
VectorStore interface — 5-method contract all backends must satisfy.

Implementations:
  ChromaDBAdapter  (chroma.py)  — wired, embedded, no server required
  Milvus           — NOT IMPLEMENTED; requires conformance tests first (fixes E-005)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models import MemoryRecord


class VectorStore(ABC):
    """Abstract vector store interface."""

    @abstractmethod
    def upsert(self, record: MemoryRecord) -> None:
        """Insert or update a MemoryRecord. Routes to correct collection by confidence."""

    @abstractmethod
    def query_semantic(
        self,
        text: str,
        top_k: int = 10,
        project: Optional[str] = None,
        thread: Optional[str] = None,
        include_inferred: bool = False,
    ) -> List[dict]:
        """
        Semantic vector search.

        Returns list of dicts:
          {record, score, decay_score, decay_policy_version}

        Default searches explicit_memories only.
        Pass include_inferred=True to also query inferred_memories (fixes E-002).
        """

    @abstractmethod
    def query_browse(
        self,
        project: Optional[str] = None,
        wing: Optional[str] = None,
        room: Optional[str] = None,
        drawer: Optional[str] = None,
        tag: Optional[str] = None,
        confidence: str = "explicit",
    ) -> List[MemoryRecord]:
        """
        Deterministic metadata-filter browse (no decay applied).
        Default scoped to active project.
        """

    @abstractmethod
    def delete_soft(self, identity_key: str, superseded_by: Optional[str] = None) -> None:
        """Tombstone a record (superseded=true). Does not physically delete."""

    @abstractmethod
    def query_hybrid(
        self,
        text: str,
        top_k: int = 10,
        project: Optional[str] = None,
        thread: Optional[str] = None,
        include_inferred: bool = False,
        rrf_k: int = 60,
    ) -> List[dict]:
        """
        Hybrid BM25 + semantic search via RRF merge.

        Runs query_semantic() and FTS5 BM25 in parallel, merges via
        Reciprocal Rank Fusion. Falls back to semantic-only if FTS
        index is empty or unavailable.

        Returns same shape as query_semantic() plus "rrf_score" key.
        """

    @abstractmethod
    def count(self, collection: str = "explicit_memories") -> int:
        """Return document count for a collection."""

    @abstractmethod
    def fetch_by_id(self, identity_key: str) -> Optional[MemoryRecord]:
        """Exact fetch by identity_key across collections. Returns None if absent.

        Second tier of the cheap-stub -> full-detail retrieval split.
        """
