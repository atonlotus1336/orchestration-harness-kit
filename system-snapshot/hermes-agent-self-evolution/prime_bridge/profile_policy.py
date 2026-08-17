"""Central Prime availability policy and non-destructive Hermes profile overlay."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MODES = frozenset({"native-only", "prime-available", "prime-default"})
PRESERVED_SECTIONS = ("providers", "mcp_servers", "memory", "toolsets")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ProfilePolicyError(RuntimeError):
    pass


def _closed_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ProfilePolicyError(f"{label} keys are not closed")


@dataclass(frozen=True)
class PrimeProfilePolicy:
    enabled: bool
    default_mode: str
    profile_modes: dict[str, str]
    mcp_server: dict[str, Any]
    native_fallback: bool
    activation: str
    explicit_handoff_required: bool

    @classmethod
    def load(cls, path: Path) -> PrimeProfilePolicy:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProfilePolicyError("Prime profile policy is unavailable") from exc
        if not isinstance(value, dict):
            raise ProfilePolicyError("Prime profile policy must be an object")
        _closed_keys(value, {
            "schema_version", "enabled", "default_mode", "profile_modes", "mcp_server",
            "native_fallback", "activation", "explicit_handoff_required",
        }, "policy")
        if value["schema_version"] != "1.0" or type(value["enabled"]) is not bool or value["default_mode"] not in MODES:
            raise ProfilePolicyError("unsupported Prime profile policy")
        modes = value["profile_modes"]
        if not isinstance(modes, dict) or any(
            not isinstance(name, str) or not PROFILE_RE.fullmatch(name) or mode not in MODES
            for name, mode in modes.items()
        ):
            raise ProfilePolicyError("invalid profile mode override")
        server = value["mcp_server"]
        if not isinstance(server, dict):
            raise ProfilePolicyError("mcp_server must be an object")
        _closed_keys(server, {"name", "trust", "command", "args"}, "mcp_server")
        if (
            server.get("name") != "prime-agent"
            # This is a local, pinned controller MCP.  Hermes names its trusted
            # transport level "full".  It lets the gateway acknowledge governed
            # auto-routes without an unusable terminal prompt; Prime's own
            # containment and capability gates still control what the resulting
            # job may do.
            or server.get("trust") != "full"
            or not isinstance(server.get("command"), str)
            or not server["command"]
            or not isinstance(server.get("args"), list)
            or not all(isinstance(item, str) and item for item in server["args"])
        ):
            raise ProfilePolicyError("invalid managed Prime MCP server")
        if value["activation"] != "restart-boundary":
            raise ProfilePolicyError("Prime activation must be restart-boundary based")
        if value["native_fallback"] is not True or value["explicit_handoff_required"] is not True:
            raise ProfilePolicyError("native fallback and explicit handoff are mandatory")
        return cls(
            enabled=value["enabled"], default_mode=value["default_mode"], profile_modes=dict(modes),
            mcp_server=copy.deepcopy(server), native_fallback=True,
            activation=value["activation"], explicit_handoff_required=True,
        )

    def mode_for(self, profile: str) -> str:
        if not PROFILE_RE.fullmatch(profile):
            raise ProfilePolicyError("profile name is not canonical")
        return self.profile_modes.get(profile, self.default_mode)

    def permits(self, profile: str) -> bool:
        return self.enabled and self.mode_for(profile) != "native-only"


def restriction_projection(config: Mapping[str, Any]) -> dict[str, Any]:
    """Project only restriction-bearing, non-secret profile policy fields."""

    projection: dict[str, Any] = {}
    for key in (
        "toolsets", "mcp_servers_disabled", "platform_toolsets", "disabled_toolsets",
        "tools", "platform", "channel", "channels",
    ):
        if key in config:
            projection[key] = copy.deepcopy(config[key])
    encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {"policy": projection, "fingerprint": hashlib.sha256(encoded).hexdigest()}


def unmanaged_mcp_servers(servers: Mapping[str, Any]) -> dict[str, Any]:
    """Return MCP servers outside the single managed Prime overlay entry."""

    if not isinstance(servers, Mapping):
        raise ProfilePolicyError("mcp_servers must be a mapping")
    return copy.deepcopy({key: value for key, value in servers.items() if key != "prime-agent"})


def render_profile_overlay(
    config: Mapping[str, Any], *, profile: str, policy: PrimeProfilePolicy,
    policy_path: Path, ledger_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a new config and receipt; never mutate the caller's mapping."""

    if not isinstance(config, Mapping):
        raise ProfilePolicyError("Hermes profile config must be a mapping")
    before = copy.deepcopy(dict(config))
    rendered = copy.deepcopy(before)
    mode = policy.mode_for(profile)
    servers = rendered.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ProfilePolicyError("mcp_servers must be a mapping")
    if not policy.enabled or mode == "native-only":
        servers.pop(policy.mcp_server["name"], None)
    else:
        server = {
            "command": policy.mcp_server["command"],
            "args": list(policy.mcp_server["args"]),
            "trust": policy.mcp_server["trust"],
            "env": {
                "HERMES_PRIME_PROFILE": profile,
                "HERMES_PRIME_MODE": mode,
                "HERMES_PRIME_POLICY_PATH": str(policy_path),
                "HERMES_PRIME_LEDGER": str(ledger_path),
                "HERMES_PRIME_PROFILE_CONFIG": f"/home/__LINUX_USER__/.hermes/profiles/{profile}/config.yaml",
                # Hermes sanitizes inherited environment variables for stdio
                # MCP servers. Carry the approved containment identity in the
                # managed entry so the Prime worker can prove its WSL boundary.
                "WSL_DISTRO_NAME": "Ubuntu-restored",
            },
        }
        servers[policy.mcp_server["name"]] = server
    for key in PRESERVED_SECTIONS:
        if key == "mcp_servers":
            continue
        if before.get(key) != rendered.get(key):
            raise ProfilePolicyError(f"overlay changed preserved section {key}")
    restriction = restriction_projection(before)
    receipt = {
        "schema_version": "1.0", "profile": profile, "mode": mode,
        "prime_available": policy.enabled and mode != "native-only", "native_fallback": True,
        "activation": "restart-boundary", "explicit_handoff_required": True,
        "restriction_fingerprint": restriction["fingerprint"],
    }
    return rendered, receipt


def render_file(
    source: Path, destination: Path, *, profile: str, policy_path: Path,
    ledger_path: Path, receipt_path: Path | None = None,
) -> dict[str, Any]:
    policy = PrimeProfilePolicy.load(policy_path)
    value = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    rendered, receipt = render_profile_overlay(
        value, profile=profile, policy=policy, policy_path=policy_path, ledger_path=ledger_path,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(rendered, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if receipt_path is not None:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def environment_context() -> tuple[str, str, Path, Path, Path]:
    profile = os.environ.get("HERMES_PRIME_PROFILE", "")
    mode = os.environ.get("HERMES_PRIME_MODE", "")
    policy = Path(os.environ.get("HERMES_PRIME_POLICY_PATH", ""))
    ledger = Path(os.environ.get("HERMES_PRIME_LEDGER", ""))
    config = Path(os.environ.get("HERMES_PRIME_PROFILE_CONFIG", ""))
    if not profile or mode not in MODES or not all(str(path) for path in (policy, ledger, config)):
        raise ProfilePolicyError("managed Prime MCP environment is incomplete")
    return profile, mode, policy, ledger, config
