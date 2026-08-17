"""Host-only MCP caller for policy-selected Hermes servers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml


class HermesMCPClientError(RuntimeError):
    pass


VARIABLE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class HermesMCPClient:
    def __init__(self, config_path: Path, *, environment: dict[str, str] | None = None):
        self.config_path = config_path
        self.environment = dict(os.environ if environment is None else environment)

    def _servers(self) -> dict[str, Any]:
        value = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        servers = value.get("mcp_servers")
        if not isinstance(servers, dict):
            raise HermesMCPClientError("Hermes MCP configuration is unavailable")
        return servers

    def configured(self) -> tuple[str, ...]:
        return tuple(sorted(self._servers()))

    def _stdio(self, server: str) -> tuple[str, list[str], dict[str, str]]:
        spec = self._servers().get(server)
        if not isinstance(spec, dict):
            raise HermesMCPClientError("MCP server is not configured")
        command = spec.get("command")
        arguments = spec.get("args", [])
        declared = spec.get("env", {})
        if not isinstance(command, str) or not command or not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            raise HermesMCPClientError("MCP server is not a supported stdio target")
        if not isinstance(declared, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in declared.items()):
            raise HermesMCPClientError("MCP server environment is invalid")
        environment = {
            key: self.environment[key] for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR")
            if key in self.environment
        }
        for key, value in declared.items():
            match = VARIABLE.fullmatch(value)
            if match:
                if match.group(1) not in self.environment:
                    raise HermesMCPClientError("MCP server authentication is unavailable")
                environment[key] = self.environment[match.group(1)]
            else:
                environment[key] = value
        return command, arguments, environment

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        command, args, environment = self._stdio(server)
        parameters = StdioServerParameters(command=command, args=args, env=environment)
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            if tool not in {item.name for item in listed.tools}:
                raise HermesMCPClientError("policy target is not advertised by MCP server")
            result = await session.call_tool(tool, arguments)
        if result.isError:
            raise HermesMCPClientError("MCP tool returned an error")
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured
        output: list[Any] = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text is None:
                output.append({"type": getattr(item, "type", "content")})
                continue
            try:
                output.append(json.loads(text))
            except json.JSONDecodeError:
                output.append(text)
        return output[0] if len(output) == 1 else output
