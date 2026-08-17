"""Strict RFC 8785 canonicalization for Phase 1 signed evidence.

All attestation producers and consumers call this module.  It intentionally
wraps exactly one implementation: the repository-locked ``rfc8785==0.1.4``.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import sys
from pathlib import Path
from typing import Any

import rfc8785


PINNED_VERSION = "0.1.4"
MAX_SAFE_INTEGER = (1 << 53) - 1


class CanonicalizationError(ValueError):
    """Raised when input is outside the RFC 8785 / I-JSON contract."""


def _assert_pinned_implementation() -> None:
    installed = importlib.metadata.version("rfc8785")
    if installed != PINNED_VERSION:
        raise CanonicalizationError(
            f"rfc8785 version mismatch: expected {PINNED_VERSION}, got {installed}"
        )


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CanonicalizationError(f"duplicate JSON property: {key!r}")
        value[key] = item
    return value


def _reject_constant(token: str) -> None:
    raise CanonicalizationError(f"non-finite JSON number: {token}")


def _validate(value: Any, location: str = "$") -> None:
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise CanonicalizationError(f"lone Unicode surrogate at {location}")
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise CanonicalizationError(f"unsafe integer at {location}: {value}")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"non-finite number at {location}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string property name at {location}")
            _validate(key, f"{location}.<key>")
            _validate(item, f"{location}.{key}")
        return
    raise CanonicalizationError(f"unsupported JSON value at {location}: {type(value).__name__}")


def loads_strict(data: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate names and non-I-JSON values."""

    try:
        value = json.loads(
            data,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except CanonicalizationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CanonicalizationError(f"invalid JSON: {error}") from error
    _validate(value)
    return value


def canonicalize(value: Any) -> bytes:
    """Return the sole canonical UTF-8 representation used for signatures."""

    _assert_pinned_implementation()
    _validate(value)
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError) as error:
        raise CanonicalizationError(str(error)) from error


def canonicalize_json(data: str | bytes) -> bytes:
    return canonicalize(loads_strict(data))


def _main() -> int:
    parser = argparse.ArgumentParser(description="Strict locked RFC 8785 canonicalizer")
    parser.add_argument("input", nargs="?", type=Path, help="JSON input path; stdin when omitted")
    parser.add_argument("--output", type=Path, help="write canonical bytes to this path")
    arguments = parser.parse_args()
    raw = arguments.input.read_bytes() if arguments.input else sys.stdin.buffer.read()
    try:
        output = canonicalize_json(raw)
        if arguments.output:
            arguments.output.write_bytes(output)
        else:
            sys.stdout.buffer.write(output)
    except CanonicalizationError as error:
        print(f"prime jcs: FAIL: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
