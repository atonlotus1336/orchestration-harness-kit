"""Tiny secret-free client installed into the contained Prime tmpfs."""

from __future__ import annotations

import json
import re
import socket
import struct
from pathlib import Path
from typing import Any

FORBIDDEN_KEY = re.compile(r"(?:api.?key|authorization|credential|password|secret|token)", re.IGNORECASE)
ALIAS = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
MAX_FRAME = 65_536


def _exact(reader: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = reader.recv(size - len(chunks))
        if not chunk:
            raise RuntimeError("Hermes capability relay closed")
        chunks.extend(chunk)
    return bytes(chunks)


def call(
    socket_path: str | Path, alias: str, arguments: dict[str, Any],
    request_id: str | None = None, timeout: float = 30.0, **identity: Any,
) -> dict[str, Any]:
    if identity:
        raise ValueError("capability identity is broker-owned")
    if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
        raise ValueError("invalid capability alias")
    if not isinstance(arguments, dict) or any(FORBIDDEN_KEY.search(str(key)) for key in arguments):
        raise ValueError("secret-shaped capability arguments are forbidden")
    request: dict[str, Any] = {"alias": alias, "arguments": arguments}
    if request_id is not None:
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("invalid capability request ID")
        request["request_id"] = request_id
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > MAX_FRAME:
        raise ValueError("capability request exceeds bounded frame")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall(b"\x00\x00" + struct.pack("!I", len(encoded)) + encoded)
        size = struct.unpack("!I", _exact(client, 4))[0]
        if size < 2 or size > MAX_FRAME:
            raise RuntimeError("invalid Hermes capability response")
        response = json.loads(_exact(client, size))
    if not isinstance(response, dict):
        raise RuntimeError("invalid Hermes capability response")  # noqa: TRY004 - protocol failure
    return response
