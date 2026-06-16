"""
MemoryWeaver — Minimum-Surface Memory Ledger for Claude Code.

Lineage:
  Absorbs both Memsearch (session capture, /memory-recall, Stop-hook pipeline)
  and MemPalace (ChromaDB backend, wing/room/drawer taxonomy).
  Adds: BM25+semantic hybrid search (SQLite FTS5 + RRF), versioned decay
  scoring, explicit/inferred confidence split, dedup identity keys, tombstones.

  MemPalace v3.3.3 remains installed (pip editable) but is not actively used.
  See ~/.claude/DUAL-MEMORY-STRATEGY.md for full history.

Components:
  parser       — Convert session *.md / DECISIONS.md to MemoryRecord
  store        — VectorStore interface + ChromaDB adapter + FTS5 adapter + RRF merge
  decay        — Query-time decay scorer (versioned)
  commands     — /memory-recall + /memory-browse slash commands
  producer     — Spool-file ProducerHook
  migrate      — Idempotent MigrationRunner for existing session files
"""

__version__ = "0.1.0"
