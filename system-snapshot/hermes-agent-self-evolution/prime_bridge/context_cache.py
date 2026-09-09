"""Content-addressed cache for stable, read-only context artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


class ContextCacheError(RuntimeError):
    pass


_HEX = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_KEY = re.compile(r"(?:authorization|credential|password|secret|api.?key|provider.?token)", re.I)
KEY_FIELDS = frozenset({"artifact_type", "workspace_revision", "policy_revision", "route_fingerprint", "input_hash", "schema_version"})


def sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _contains_sensitive_key(value: Any) -> bool:
    """Reject cache payloads with sensitive keys at any nesting depth."""
    if isinstance(value, Mapping):
        return any(
            _SENSITIVE_KEY.search(str(key)) or _contains_sensitive_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def workspace_fingerprint(workspace: Path, declared_paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    root = workspace.resolve()
    for relative in sorted(declared_paths):
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ContextCacheError("declared path escapes workspace") from exc
        digest.update(relative.encode())
        if candidate.is_file():
            digest.update(hashlib.sha256(candidate.read_bytes()).digest())
        else:
            digest.update(b"MISSING")
    return digest.hexdigest()


class ContextCachePolicy:
    def __init__(self, *, enabled: bool, max_entry_bytes: int,
                 allowed_artifact_types: Sequence[str], forbidden_artifact_types: Sequence[str]):
        if not isinstance(enabled, bool) or type(max_entry_bytes) is not int or max_entry_bytes <= 0:
            raise ContextCacheError("cache policy limits are invalid")
        allowed = tuple(allowed_artifact_types)
        forbidden = tuple(forbidden_artifact_types)
        if not allowed or len(allowed) != len(set(allowed)) or len(forbidden) != len(set(forbidden)):
            raise ContextCacheError("cache artifact lists are invalid")
        if set(allowed) & set(forbidden) or any(not isinstance(item, str) or not item for item in allowed + forbidden):
            raise ContextCacheError("cache artifact types overlap or are invalid")
        self.enabled = enabled
        self.max_entry_bytes = max_entry_bytes
        self.allowed_artifact_types = frozenset(allowed)
        self.forbidden_artifact_types = frozenset(forbidden)

    @classmethod
    def load(cls, path: str | Path) -> "ContextCachePolicy":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContextCacheError("cache policy is unreadable") from exc
        expected = {"schema_version", "enabled", "max_entry_bytes", "allowed_artifact_types", "forbidden_artifact_types"}
        if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != "1.0":
            raise ContextCacheError("cache policy is not a supported closed schema")
        return cls(enabled=value["enabled"], max_entry_bytes=value["max_entry_bytes"],
                   allowed_artifact_types=value["allowed_artifact_types"],
                   forbidden_artifact_types=value["forbidden_artifact_types"])


class ContextCache:
    def __init__(self, root: str | Path, policy: ContextCachePolicy):
        self.root = Path(root)
        self.policy = policy
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, fields: Mapping[str, Any]) -> str:
        if not isinstance(fields, Mapping) or set(fields) != KEY_FIELDS:
            raise ContextCacheError("cache key fields are not closed")
        if fields["artifact_type"] not in self.policy.allowed_artifact_types:
            raise ContextCacheError("artifact type is not cacheable")
        for name in ("workspace_revision", "policy_revision", "route_fingerprint", "input_hash"):
            if not isinstance(fields[name], str) or not _HEX.fullmatch(fields[name]):
                raise ContextCacheError(f"{name} must be a sha256 digest")
        if fields["schema_version"] != "1.0":
            raise ContextCacheError("cache key schema is unsupported")
        return sha256_json(dict(fields))

    def _path(self, key: str) -> Path:
        if not _HEX.fullmatch(key):
            raise ContextCacheError("cache key is invalid")
        return self.root / f"{key}.json"

    def put(self, fields: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any]:
        if not self.policy.enabled:
            return {"status": "DISABLED", "key_fingerprint": None, "bytes": 0}
        key = self.key(fields)
        if not isinstance(value, Mapping) or _contains_sensitive_key(value):
            raise ContextCacheError("cache value contains forbidden keys")
        envelope = {"key": dict(fields), "value": dict(value), "value_sha256": sha256_json(dict(value))}
        payload = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(payload) > self.policy.max_entry_bytes:
            raise ContextCacheError("cache entry exceeds size limit")
        target = self._path(key)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary_name).replace(target)
        finally:
            temporary = Path(temporary_name)
            if temporary.exists():
                temporary.unlink()
        return {"status": "STORED", "key_fingerprint": key, "bytes": len(payload)}

    def get(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        if not self.policy.enabled:
            return {"status": "DISABLED", "value": None, "key_fingerprint": None, "bytes_reused": 0, "reason": "POLICY_DISABLED"}
        key = self.key(fields)
        path = self._path(key)
        if not path.is_file():
            return {"status": "MISS", "value": None, "key_fingerprint": key, "bytes_reused": 0, "reason": "NOT_FOUND"}
        try:
            payload = path.read_bytes()
            envelope = json.loads(payload)
            if set(envelope) != {"key", "value", "value_sha256"} or envelope["key"] != dict(fields):
                raise ContextCacheError("cache envelope binding is invalid")
            if envelope["value_sha256"] != sha256_json(envelope["value"]):
                raise ContextCacheError("cache content hash is invalid")
            if not isinstance(envelope["value"], Mapping) or _contains_sensitive_key(envelope["value"]):
                raise ContextCacheError("cache value contains forbidden keys")
        except (OSError, json.JSONDecodeError, ContextCacheError):
            path.unlink(missing_ok=True)
            return {"status": "MISS", "value": None, "key_fingerprint": key, "bytes_reused": 0, "reason": "CORRUPT"}
        return {"status": "HIT", "value": envelope["value"], "key_fingerprint": key, "bytes_reused": len(payload), "reason": "VALID"}
