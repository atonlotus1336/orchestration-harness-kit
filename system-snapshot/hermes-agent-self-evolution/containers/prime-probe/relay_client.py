#!/usr/bin/env python3
"""Fixed loopback-to-UDS raw TLS forwarder for the contained Prime probe."""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import struct


async def copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def serve(config: dict) -> None:
    hostname = config["allowed_hostname"].encode("ascii")
    port = config["allowed_port"]

    async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        relay_reader, relay_writer = await asyncio.open_unix_connection(config["uds_path"])
        relay_writer.write(struct.pack("!H", len(hostname)) + hostname + struct.pack("!H", port))
        await relay_writer.drain()
        await asyncio.gather(copy(client_reader, relay_writer), copy(relay_reader, client_writer))

    server = await asyncio.start_server(handle, config["listen_host"], config["listen_port"])
    async with server:
        await server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=pathlib.Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    expected = {"listen_host", "listen_port", "uds_path", "allowed_hostname", "allowed_port"}
    if set(config) != expected:
        raise SystemExit("relay config has unknown or missing fields")
    if config["listen_host"] != "127.0.0.1" or config["uds_path"] != "/run/prime-relay/relay.sock" or config["allowed_port"] != 443:
        raise SystemExit("relay config violates the fixed boundary")
    asyncio.run(serve(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
