"""Secret-safe evidence preparation for the Phase 01 Prime RPC probe.

Credential bytes are accepted only as explicit in-memory values.  They are
never included in findings, exception text, or persisted scan metadata.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


REDACTION = "[REDACTED]"
SENSITIVE_KEY = re.compile(
    r"(?i)^(?:api[_-]?key|apikey|authorization|password|secret|token|access[_-]?token|refresh[_-]?token)$"
)
GENERIC_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|authorization|password|secret|token|access[_-]?token|refresh[_-]?token)"
        r"\s*[:=]\s*[\"']?[^\s,;\"'}\]]{4,}"
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


@dataclasses.dataclass(frozen=True)
class PreparedEvidence:
    """Sanitized value plus non-disclosing scan receipts."""

    value: Any
    scans: dict[str, Any]
    persisted_bytes: bytes


def named_credentials(environment: Mapping[str, str], names: Iterable[str]) -> tuple[str, ...]:
    """Read only the named environment references and ignore empty values."""

    values = {
        value
        for name in names
        if isinstance(name, str)
        for value in (environment.get(name),)
        if isinstance(value, str) and value
    }
    return tuple(sorted(values, key=lambda item: (-len(item), item)))


def _replace_exact(text: str, credentials: tuple[str, ...]) -> str:
    for credential in credentials:
        text = text.replace(credential, REDACTION)
    return text


def _replace_generic(text: str) -> str:
    for pattern in GENERIC_PATTERNS:
        text = pattern.sub(REDACTION, text)
    return text


def sanitize_value(value: Any, credentials: Iterable[str] = ()) -> Any:
    """Recursively sanitize exact credentials first, then generic shapes."""

    ordered = tuple(sorted({item for item in credentials if item}, key=lambda item: (-len(item), item)))

    def visit(item: Any, key: str | None = None) -> Any:
        if key is not None and SENSITIVE_KEY.fullmatch(key):
            if item in (None, "", [], {}):
                return item
            return REDACTION
        if isinstance(item, Mapping):
            return {str(child_key): visit(child_value, str(child_key)) for child_key, child_value in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [visit(child) for child in item]
        if isinstance(item, BaseException):
            return _replace_generic(_replace_exact(str(item), ordered))
        if isinstance(item, str):
            return _replace_generic(_replace_exact(item, ordered))
        return item

    return visit(value)


def _count_exact(data: bytes, credentials: tuple[str, ...]) -> int:
    remaining = data
    count = 0
    for value in credentials:
        encoded = value.encode("utf-8")
        occurrences = remaining.count(encoded)
        count += occurrences
        if occurrences:
            remaining = remaining.replace(encoded, b"")
    return count


def _count_generic(data: bytes) -> int:
    text = data.decode("utf-8", errors="replace")
    return sum(len(pattern.findall(text)) for pattern in GENERIC_PATTERNS)


def prepare_evidence(value: Any, credentials: Iterable[str] = ()) -> PreparedEvidence:
    """Scan unsanitized bytes in memory and return only sanitized evidence.

    The raw serialization is never returned. Exact scan metadata contains only
    counts, never values, prefixes, hashes, or environment contents.
    """

    ordered = tuple(sorted({item for item in credentials if item}, key=lambda item: (-len(item), item)))
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    raw_exact_matches = _count_exact(raw, ordered)

    sanitized = sanitize_value(value, ordered)
    persisted = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    persisted_exact_matches = _count_exact(persisted, ordered)
    generic_matches = _count_generic(persisted)

    exact_status = "NOT_APPLICABLE" if not ordered else ("PASS" if persisted_exact_matches == 0 else "FAIL")
    scans = {
        "generic_scan": {
            "status": "PASS" if generic_matches == 0 else "FAIL",
            "matches": generic_matches,
        },
        "exact_scan": {
            "status": exact_status,
            "values_supplied": len(ordered),
            "raw_matches": raw_exact_matches,
            "persisted_matches": persisted_exact_matches,
        },
    }
    return PreparedEvidence(sanitized, scans, persisted)


def scan_persisted_bytes(data: bytes, credentials: Iterable[str] = ()) -> dict[str, Any]:
    """Re-scan final artifact bytes without returning any matched material."""

    ordered = tuple(sorted({item for item in credentials if item}, key=lambda item: (-len(item), item)))
    exact_matches = _count_exact(data, ordered)
    generic_matches = _count_generic(data)
    return {
        "generic_scan": {"status": "PASS" if generic_matches == 0 else "FAIL", "matches": generic_matches},
        "exact_scan": {
            "status": "NOT_APPLICABLE" if not ordered else ("PASS" if exact_matches == 0 else "FAIL"),
            "values_supplied": len(ordered),
            "persisted_matches": exact_matches,
        },
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_repo_path(value: str) -> str:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() in {"", "."}:
        raise ValueError(f"unsafe manifest path: {value!r}")
    return candidate.as_posix()


def _owned_paths(root: Path) -> list[str]:
    from scripts.phase1_gap_commit_gate import compile_allowed, load_json

    phase = root / ".planning/phases/01-contracts-feasibility"
    allowed = load_json(phase / "PHASE1-GAP-ALLOWED-PATHS.json")
    paths, _deletions = compile_allowed(root, allowed)
    return sorted(paths)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_owned_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    """Build the closed Phase 01 path inventory without embedding secret material."""

    root = root.resolve()
    relative_manifest = manifest_path.resolve(strict=False).relative_to(root).as_posix()
    paths = _owned_paths(root)
    if relative_manifest not in paths:
        raise ValueError("manifest path is outside the Phase 01 owned set")
    entries = []
    for relative in paths:
        path = root / relative
        present = relative == relative_manifest or path.is_file()
        entries.append({
            "path": relative,
            "presence": "PRESENT" if present else "FUTURE",
            "sha256": None if relative == relative_manifest or not present else _sha256(path.read_bytes()),
        })
    value = {
        "schema_version": "1.0",
        "phase": "01-contracts-feasibility",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "closed_world": True,
        "owned_paths_sha256": _sha256(("\n".join(paths) + "\n").encode("utf-8")),
        "scan_policy": {
            "generic": "ALWAYS",
            "exact": "WHEN_NONEMPTY_RUNTIME_VALUES_ARE_SUPPLIED",
            "matched_material_persisted": False,
        },
        "entries": entries,
    }
    _atomic_json(manifest_path, value)
    return value


def scan_owned_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate the closed inventory and scan every currently present owned artifact."""

    root = root.resolve()
    relative_manifest = manifest_path.resolve(strict=True).relative_to(root).as_posix()
    manifest_bytes = manifest_path.read_bytes()
    value = json.loads(manifest_bytes)
    required = {"schema_version", "phase", "generated_at", "closed_world", "owned_paths_sha256", "scan_policy", "entries"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("closed manifest shape mismatch")
    if value["schema_version"] != "1.0" or value["phase"] != "01-contracts-feasibility" or value["closed_world"] is not True:
        raise ValueError("closed manifest identity mismatch")

    entries = value["entries"]
    if not isinstance(entries, list):
        raise ValueError("manifest entries must be a list")
    normalized = [_normalize_repo_path(str(entry.get("path", ""))) for entry in entries if isinstance(entry, dict)]
    expected = _owned_paths(root)
    if len(normalized) != len(entries) or normalized != expected or len(normalized) != len(set(normalized)):
        raise ValueError("manifest does not exactly match the owned Phase 01 path set")
    if value["owned_paths_sha256"] != _sha256(("\n".join(expected) + "\n").encode("utf-8")):
        raise ValueError("owned path-set hash mismatch")

    runtime_values = tuple(sorted({
        item for name, item in os.environ.items()
        if item and re.search(r"(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY)$", name, re.I)
    }, key=lambda item: (-len(item), item)))
    generic_matches = 0
    exact_matches = 0
    scanned = 0
    for entry, relative in zip(entries, normalized, strict=True):
        if set(entry) != {"path", "presence", "sha256"} or entry["presence"] not in {"PRESENT", "FUTURE"}:
            raise ValueError(f"invalid manifest entry: {relative}")
        path = root / relative
        if entry["presence"] == "FUTURE":
            if path.exists():
                raise ValueError(f"future artifact now exists; refresh manifest: {relative}")
            if entry["sha256"] is not None:
                raise ValueError(f"future artifact has a digest: {relative}")
            continue
        if not path.is_file():
            raise ValueError(f"present artifact is missing: {relative}")
        data = manifest_bytes if relative == relative_manifest else path.read_bytes()
        if relative != relative_manifest and entry["sha256"] != _sha256(data):
            raise ValueError(f"present artifact digest mismatch: {relative}")
        if relative == relative_manifest and entry["sha256"] is not None:
            raise ValueError("self-referential manifest digest must be null")
        if "/evidence/" not in f"/{relative}":
            generic_count = sum(
                len(pattern.findall(data.decode("utf-8", errors="replace")))
                for index, pattern in enumerate(GENERIC_PATTERNS)
                if index != 2
            )
            exact_count = _count_exact(data, runtime_values)
            receipt = {
                "generic_scan": {"status": "PASS" if generic_count == 0 else "FAIL", "matches": generic_count},
                "exact_scan": {
                    "status": "NOT_APPLICABLE" if not runtime_values else ("PASS" if exact_count == 0 else "FAIL"),
                    "values_supplied": len(runtime_values),
                    "persisted_matches": exact_count,
                },
            }
        else:
            receipt = scan_persisted_bytes(data, runtime_values)
        if receipt["generic_scan"]["status"] != "PASS":
            raise ValueError(f"generic secret pattern found in owned artifact: {relative}")
        if receipt["exact_scan"]["status"] == "FAIL":
            raise ValueError(f"runtime secret value found in owned artifact: {relative}")
        generic_matches += int(receipt["generic_scan"]["matches"])
        exact_matches += int(receipt["exact_scan"]["persisted_matches"])
        scanned += 1

    result = {
        "schema_version": "1.0",
        "status": "PASS" if generic_matches == 0 and exact_matches == 0 else "FAIL",
        "files_scanned": scanned,
        "owned_paths": len(expected),
        "generic_scan": {"status": "PASS" if generic_matches == 0 else "FAIL", "matches": generic_matches},
        "exact_scan": {
            "status": "NOT_APPLICABLE" if not runtime_values else ("PASS" if exact_matches == 0 else "FAIL"),
            "values_supplied": len(runtime_values),
            "persisted_matches": exact_matches,
        },
    }
    if result["status"] != "PASS":
        raise ValueError("owned artifact secret scan failed")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or scan the closed Phase 01 owned-artifact manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=("generic",), default="generic")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    try:
        if args.build:
            build_owned_manifest(root, args.manifest)
        result = scan_owned_manifest(root, args.manifest)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"phase01 evidence scan: FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
