"""
Central path configuration for MemoryWeaver data storage.

All data (ChromaDB, FTS, spool, decay config, migration log) resolves
under MW_DATA_DIR.

Override via env var:
    MEMORYWEAVER_DATA_DIR=G:/MyVault/.claude/memory/memoryweaver

Default (unchanged from original):
    ~/.claude/Memory/memoryweaver
"""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_data_dir() -> Path:
    env = os.environ.get("MEMORYWEAVER_DATA_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".claude" / "Memory" / "memoryweaver"


# Single import point for all modules
MW_DATA_DIR: Path = _resolve_data_dir()
