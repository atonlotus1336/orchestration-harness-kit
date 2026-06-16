"""
Phase 4 — recent-memory digest snapshot (the "read-on-write" read side).

Written cheaply on ingest/Stop (`write_recent_digest`), read instantly by each
harness's SessionStart hook (no chromadb import on the read path). This is the
read-before-you-work complement to the Stop-spool write-on-stop.

NON-GENERATIVE: the digest is a deterministic list of recent stub projections.
Quarantined records are excluded (don't surface untrusted memory as context).

See .claude/memoryweaver/stub-tier-plan/PLAN.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from ._paths import MW_DATA_DIR
from .stub import project as _project

DIGEST_FILENAME = "recent-digest.json"
DIGEST_PATH = MW_DATA_DIR / DIGEST_FILENAME
_DEFAULT_N = 8


def _store_default():
    from .store.chroma import ChromaDBAdapter
    return ChromaDBAdapter()


def write_recent_digest(store=None, n: int = _DEFAULT_N, path=None) -> dict:
    """
    Write the top-`n` most-recent NON-quarantined live memories as compact stubs
    to recent-digest.json (atomic). Fail-open: never raises. Returns the payload.
    """
    store = store or _store_default()
    out_path = Path(path) if path else DIGEST_PATH

    try:
        recs = store.query_browse(confidence="all")  # excludes superseded
    except Exception:
        recs = []
    recs = [r for r in recs if getattr(r, "provenance_status", "valid") != "quarantined"]
    recs.sort(key=lambda r: r.timestamp, reverse=True)

    stubs = []
    for r in recs[:n]:
        try:
            s = _project(r).to_dict()
            s["ts"] = r.timestamp.isoformat()
            stubs.append(s)
        except Exception:
            continue

    payload = {
        "schema": "recent-digest/1",
        "note": "written on ingest; read by per-harness SessionStart hooks",
        "count": len(stubs),
        "stubs": stubs,
    }

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, out_path)  # atomic publish
    except Exception:
        pass  # snapshot failure must never block ingest/session end

    return payload


def read_recent_digest(path=None) -> Optional[dict]:
    """Read the snapshot (used by tests / tools). None if absent or unreadable."""
    out_path = path or DIGEST_PATH
    try:
        if not os.path.exists(out_path):
            return None
        with open(out_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
