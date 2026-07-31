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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ._paths import MW_DATA_DIR
from .stub import project as _project

DIGEST_FILENAME = "recent-digest.json"
DIGEST_PATH = MW_DATA_DIR / DIGEST_FILENAME
BEACON_FILENAME = "freshness-beacon.json"
BEACON_PATH = MW_DATA_DIR / BEACON_FILENAME
_DEFAULT_N = 8
_DEFAULT_MAX_AGE_SECONDS = 2 * 60 * 60


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
        write_freshness_beacon(path=BEACON_PATH, digest_path=out_path)
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


def write_freshness_beacon(
    last_digest_ts: Optional[datetime] = None,
    last_ingest_ts: Optional[datetime] = None,
    last_success: bool = True,
    path=None,
    digest_path=None,
) -> dict:
    """Write the reader-side freshness beacon used by SessionStart hooks."""
    out_path = Path(path) if path else BEACON_PATH
    now = datetime.now(tz=timezone.utc)
    last_digest_ts = last_digest_ts or now
    last_ingest_ts = last_ingest_ts or last_digest_ts
    payload = {
        "schema": "memoryweaver-freshness/1",
        "last_ingest_ts": last_ingest_ts.isoformat(),
        "last_digest_ts": last_digest_ts.isoformat(),
        "last_success": bool(last_success),
        "digest_path": str(digest_path or DIGEST_PATH),
        "capture_status_path": str(MW_DATA_DIR / "capture-status.json"),
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, out_path)
    except Exception:
        pass
    return payload


def read_freshness_beacon(path=None) -> Optional[dict]:
    out_path = Path(path) if path else BEACON_PATH
    try:
        if not out_path.exists():
            return None
        data = json.loads(out_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def is_digest_fresh(now: Optional[datetime] = None, max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS, path=None) -> bool:
    beacon = read_freshness_beacon(path=path)
    if not beacon or not beacon.get("last_success"):
        return False
    raw_ts = beacon.get("last_digest_ts")
    if not raw_ts:
        return False
    try:
        ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(tz=timezone.utc)
    return (now - ts).total_seconds() <= max_age_seconds
