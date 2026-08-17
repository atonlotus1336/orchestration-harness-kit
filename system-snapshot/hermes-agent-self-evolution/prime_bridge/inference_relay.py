"""Endpoint-pinned raw TLS relay for a network-isolated Prime container."""

from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
import json
import os
import socket
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Self


class RelayPolicyError(RuntimeError):
    """The requested or resolved endpoint escaped the fixed relay policy."""


@dataclasses.dataclass(frozen=True)
class RelayPolicy:
    hostname: str
    allowed_ips: tuple[str, ...]
    port: int = 443

    def __post_init__(self) -> None:
        canonical = self.hostname.encode("idna").decode("ascii").lower()
        if canonical != self.hostname or not canonical or len(canonical) > 253:
            raise RelayPolicyError("hostname must be canonical lower-case IDNA")
        cloud = self.hostname == "ollama.com" and self.port == 443
        local = self.hostname == "127.0.0.1" and self.port == 11434
        if not (cloud or local):
            raise RelayPolicyError("only the approved Ollama cloud or loopback endpoint is allowed")
        if not self.allowed_ips:
            raise RelayPolicyError("an exact IP allowlist is required")
        normalized = tuple(sorted({str(ipaddress.ip_address(value)) for value in self.allowed_ips}))
        if normalized != self.allowed_ips:
            raise RelayPolicyError("allowed IPs must be unique and canonically sorted")
        if local and normalized != ("127.0.0.1",):
            raise RelayPolicyError("loopback relay must resolve only to 127.0.0.1")
        if cloud and any(ipaddress.ip_address(value).is_loopback for value in normalized):
            raise RelayPolicyError("cloud relay cannot resolve to loopback")

    @classmethod
    def resolve(cls, hostname: str, port: int = 443) -> RelayPolicy:
        canonical = hostname.encode("idna").decode("ascii").lower()
        infos = socket.getaddrinfo(canonical, port, type=socket.SOCK_STREAM)
        values = tuple(sorted({str(ipaddress.ip_address(info[4][0])) for info in infos}))
        return cls(canonical, values, port)

    def assert_dns_unchanged(self) -> None:
        current = RelayPolicy.resolve(self.hostname) if self.port == 443 else RelayPolicy.resolve(self.hostname, self.port)
        if current.allowed_ips != self.allowed_ips:
            raise RelayPolicyError("DNS/IP drift detected; relay is closed")


async def _read_request(reader: asyncio.StreamReader, size: int) -> tuple[str, int]:
    if size < 1 or size > 253:
        raise RelayPolicyError("invalid relay hostname length")
    raw_hostname = await reader.readexactly(size)
    port = struct.unpack("!H", await reader.readexactly(2))[0]
    try:
        hostname = raw_hostname.decode("ascii").lower()
    except UnicodeDecodeError as error:
        raise RelayPolicyError("relay hostname must be ASCII") from error
    return hostname, port


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    finally:
        if not writer.is_closing():
            writer.close()


Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]
CapabilityHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class InferenceRelay:
    """A UDS listener which tunnels bytes only to one DNS-pinned HTTPS endpoint."""

    def __init__(
        self, socket_path: Path, policy: RelayPolicy, *, connector: Connector | None = None,
        capability_handler: CapabilityHandler | None = None, max_capability_bytes: int = 65_536,
    ):
        self.socket_path = socket_path
        self.policy = policy
        self.connector = connector or asyncio.open_connection
        self.capability_handler = capability_handler
        self.max_capability_bytes = max_capability_bytes
        self.server: asyncio.AbstractServer | None = None
        self.accepted = 0
        self.rejected = 0
        self.capability_accepted = 0
        self.capability_rejected = 0

    async def start(self) -> None:
        if self.socket_path.exists() or self.socket_path.is_symlink():
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
        # The parent is host-user-only (0700). The bind-mounted socket itself must
        # be writable by the remapped non-root container UID.
        os.chmod(self.socket_path, 0o666)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self.socket_path.exists() or self.socket_path.is_symlink():
            self.socket_path.unlink()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        try:
            size = struct.unpack("!H", await reader.readexactly(2))[0]
            if size == 0:
                await self._handle_capability(reader, writer)
                return
            hostname, port = await _read_request(reader, size)
            if hostname != self.policy.hostname or port != self.policy.port:
                raise RelayPolicyError("requested endpoint is not approved")
            self.policy.assert_dns_unchanged()
            upstream_reader, upstream = await self.connector(self.policy.allowed_ips[0], self.policy.port)
            self.accepted += 1
            await asyncio.gather(_copy(reader, upstream), _copy(upstream_reader, writer))
        except (RelayPolicyError, OSError, asyncio.IncompleteReadError):
            self.rejected += 1
        finally:
            if upstream is not None and not upstream.is_closing():
                upstream.close()
                await upstream.wait_closed()
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()

    async def _handle_capability(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if self.capability_handler is None:
                raise RelayPolicyError("capability relay is disabled")
            size = struct.unpack("!I", await reader.readexactly(4))[0]
            if size < 2 or size > self.max_capability_bytes:
                raise RelayPolicyError("invalid capability frame length")
            request = json.loads(await reader.readexactly(size))
            if not isinstance(request, dict) or set(request) - {"alias", "arguments", "request_id"}:
                raise RelayPolicyError("invalid capability request")
            response = await self.capability_handler(request)
            encoded = json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            if len(encoded) > self.max_capability_bytes:
                raise RelayPolicyError("capability response exceeds bound")
            writer.write(struct.pack("!I", len(encoded)) + encoded)
            await writer.drain()
            self.capability_accepted += 1
        except (RelayPolicyError, OSError, asyncio.IncompleteReadError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.capability_rejected += 1
