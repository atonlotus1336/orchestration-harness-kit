"""Shared metadata validation contract; no ledger or scheduler is created."""
from __future__ import annotations
import json, re
from datetime import datetime, timezone
from typing import Any

class LedgerError(RuntimeError):
    pass


TERMINAL = {"completed", "failed", "cancelled", "orphaned"}


CREDENTIAL_SHAPE = re.compile(r"(?:-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}\b|\bAKIA[0-9A-Z]{16}\b)")


FORBIDDEN_KEY = re.compile(r"(?:api.?key|password|secret|token)", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_safe(value: Any, *, max_bytes: int = 65_536) -> None:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(text.encode()) > max_bytes:
        raise LedgerError("job metadata exceeds the bounded storage limit")
    def keys(item: Any) -> bool:
        if isinstance(item, dict):
            return any(FORBIDDEN_KEY.search(str(key)) or keys(child) for key, child in item.items())
        if isinstance(item, list):
            return any(keys(child) for child in item)
        return False
    if CREDENTIAL_SHAPE.search(text) or keys(value):
        raise LedgerError("secret-shaped job metadata is forbidden")
