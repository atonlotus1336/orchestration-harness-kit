"""M2 repair helpers for exporting and tombstoning bad captured memories."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from ._paths import MW_DATA_DIR
from .models import MemoryRecord
from .stub import project as project_stub

BAD_GIST_RE = re.compile(
    r"(^\s*(---|#|\[|\||>)|append-only log|FABLE-PLAN\.md|PLAN\.md|"
    r"also at (the )?repo root|^- \*\*|date:\s*\d{4}-\d{2}-\d{2}\s+(project|decision):|"
    r"see `?\.claude/level5-watch\.md|memory\.md`? for memory index|"
    r"this file tracks decisions|irreversible choices and cross-project learnings|format:\s*`\[yyyy-mm-dd\])",
    re.IGNORECASE,
)


def is_bad_m2_memory(record: MemoryRecord) -> bool:
    text = " ".join((record.text or "").split())
    try:
        gist = project_stub(record).gist
    except Exception:
        gist = ""
    haystack = f"{text}\n{gist}"
    if BAD_GIST_RE.search(haystack):
        return True
    if record.memory_type == "decision" and len(text) <= 260:
        return bool(re.match(r"^\s*(---|#|\[|\||>)", text))
    return False


def find_bad_memories(store=None) -> List[MemoryRecord]:
    store = store or _store_default()
    return [r for r in store.query_browse(confidence="all") if is_bad_m2_memory(r)]


def export_bad_memories(records: Iterable[MemoryRecord], path: Optional[Path] = None) -> Path:
    out_path = Path(path) if path else _default_export_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "memoryweaver-m2-quarantine/1",
        "created_ts": datetime.now(tz=timezone.utc).isoformat(),
        "count": 0,
        "records": [],
    }
    for rec in records:
        payload["records"].append(
            {
                "identity_key": rec.identity_key,
                "source_uri": rec.source_uri,
                "memory_type": rec.memory_type,
                "confidence": rec.confidence,
                "project": rec.project,
                "timestamp": rec.timestamp.isoformat(),
                "text": rec.text,
            }
        )
    payload["count"] = len(payload["records"])
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def backup_stores() -> List[str]:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    backups: List[str] = []
    for src in (MW_DATA_DIR / "chroma", MW_DATA_DIR / "fts.db", MW_DATA_DIR / "chroma.sqlite3"):
        if not src.exists():
            continue
        dst = src.with_name(f"{src.name}.m2-{ts}.bak")
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        backups.append(str(dst))
    return backups


def purge_bad_memories(apply: bool = False, store=None, export_path: Optional[Path] = None) -> dict:
    store = store or _store_default()
    matches = find_bad_memories(store)
    out_path = export_bad_memories(matches, export_path)
    result = {
        "matched": len(matches),
        "exported": len(matches),
        "export_path": str(out_path),
        "applied": bool(apply),
        "backups": [],
        "deleted": 0,
    }
    if not apply:
        return result
    if result["matched"] != result["exported"]:
        raise RuntimeError("matched/exported count mismatch; refusing purge")
    result["backups"] = backup_stores()
    for rec in matches:
        store.delete_soft(rec.identity_key, revoked=True)
        result["deleted"] += 1
    return result


def _default_export_path() -> Path:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    if Path("/mnt/d/MyVault").exists():
        return Path("/mnt/d/MyVault/.planning/hermes-master-plan/eval") / f"quarantine-purged-{ts}.json"
    return Path(r"D:\MyVault\.planning\hermes-master-plan\eval") / f"quarantine-purged-{ts}.json"


def _store_default():
    from .store.chroma import ChromaDBAdapter

    return ChromaDBAdapter()
