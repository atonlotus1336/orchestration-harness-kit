"""Digest freshness and first-turn context helpers for vault-parity."""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_DIGEST_ITEMS = int(os.environ.get("myvault_PARITY_DIGEST_ITEMS", "8"))
MAX_AGE_SECONDS = int(os.environ.get("MEMORYWEAVER_DIGEST_MAX_AGE_SECONDS", str(2 * 60 * 60)))
INJECTED_CACHE_CAP = int(os.environ.get("myvault_PARITY_INJECT_CACHE_CAP", "4096"))

# Bounded first-turn dedup: a set for O(1) membership + a FIFO deque so a long-lived
# gateway process cannot grow this unboundedly (M5-F4).
_injected_sessions: set[str] = set()
_injected_order: deque[str] = deque()


def _mark_injected(session_id: str) -> bool:
    """Record a first-turn injection; returns False if this session was already injected."""
    if session_id in _injected_sessions:
        return False
    _injected_sessions.add(session_id)
    _injected_order.append(session_id)
    while len(_injected_order) > INJECTED_CACHE_CAP:
        evicted = _injected_order.popleft()
        _injected_sessions.discard(evicted)
    return True


def _default_data_dir() -> Path:
    if os.name == "nt":
        return Path(r"${MEMORYWEAVER_DATA_DIR}")
    return Path("${MEMORYWEAVER_DATA_DIR}")


def data_dir() -> Path:
    return Path(os.environ.get("MEMORYWEAVER_DATA_DIR") or _default_data_dir())


def digest_path() -> Path:
    return data_dir() / "recent-digest.json"


def beacon_path() -> Path:
    return data_dir() / "freshness-beacon.json"


def is_first_turn(kwargs: dict[str, Any]) -> bool:
    if "is_first_turn" in kwargs:
        return bool(kwargs["is_first_turn"])
    history = kwargs.get("conversation_history")
    if isinstance(history, list):
        return len(history) == 0
    messages = kwargs.get("messages")
    return isinstance(messages, list) and len(messages) <= 1


def session_id_from(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("session_id") or kwargs.get("task_id") or "unknown")


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        return None


def digest_fresh(now: datetime | None = None) -> tuple[bool, str]:
    path = beacon_path()
    if not path.exists():
        return False, "missing freshness beacon"
    try:
        beacon = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return False, f"unreadable freshness beacon: {exc}"
    if not beacon.get("last_success"):
        return False, "last digest refresh was not successful"
    ts = _parse_ts(beacon.get("last_digest_ts"))
    if ts is None:
        return False, "missing last_digest_ts"
    age = ((now or datetime.now(timezone.utc)) - ts).total_seconds()
    if age > MAX_AGE_SECONDS:
        return False, f"digest stale age_s={int(age)} max_s={MAX_AGE_SECONDS}"
    return True, "fresh"


def render_digest_context(payload: dict[str, Any]) -> str:
    stubs = (payload.get("stubs") or [])[:MAX_DIGEST_ITEMS]
    lines = [
        f"MemoryWeaver - {len(stubs)} recent memories via vault-parity. "
        "Use memory_stub_list / memory_recall for more; memory_fetch <id> for full text:"
    ]
    for stub in stubs:
        conf = str(stub.get("confidence") or "?")[:3]
        project = stub.get("project") or "-"
        gist = str(stub.get("gist") or "").strip()
        identity = str(stub.get("identity_key") or "")[:12]
        if gist:
            lines.append(f"- ({conf}/{project}) {gist}  .{identity}")
    return "\n".join(lines)


def first_turn_context(kwargs: dict[str, Any]) -> dict[str, Any]:
    session_id = session_id_from(kwargs)
    platform = str(kwargs.get("platform") or "")
    result: dict[str, Any] = {}
    if platform == "cron":
        result["skip_memory"] = True
    if not is_first_turn(kwargs):
        return result
    if not _mark_injected(session_id):
        return result

    fresh, reason = digest_fresh()
    if not fresh:
        result["context"] = f"MEMORY DIGEST STALE: {reason}. Do not treat prior digest context as current."
        result["vault_parity_status"] = "stale"
        return result

    path = digest_path()
    if not path.exists():
        result["context"] = "MEMORY DIGEST STALE: digest file is missing."
        result["vault_parity_status"] = "stale"
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        result["context"] = render_digest_context(payload)
        result["vault_parity_status"] = "fresh"
        return result
    except Exception as exc:
        result["context"] = f"MEMORY DIGEST STALE: digest unreadable: {exc}"
        result["vault_parity_status"] = "stale"
        return result

