"""Closed desired-topology and repair-envelope authority."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class ConnectivityPolicyError(RuntimeError):
    pass


NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
AXES = {"backend", "client", "authentication", "readiness", "tunnel", "mount", "ownership"}


def _closed(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ConnectivityPolicyError(f"{label} keys are not closed")
    return value


@dataclass(frozen=True)
class ConnectivityPolicy:
    enabled: bool
    authority_machine: str
    machines: dict[str, dict[str, Any]]
    freshness_seconds: dict[str, int]
    resources: dict[str, dict[str, Any]]
    approval_required_changes: frozenset[str]
    repair_envelopes: dict[str, dict[str, Any]]
    source_sha256: str

    @classmethod
    def load(cls, path: Path) -> "ConnectivityPolicy":
        import hashlib

        raw = path.read_bytes()
        value = _closed(json.loads(raw), {
            "schema_version", "enabled", "authority_machine", "machines",
            "freshness_seconds", "resources", "approval_required_changes",
            "repair_envelopes",
        }, "connectivity policy")
        if value["schema_version"] != "1.0" or type(value["enabled"]) is not bool:
            raise ConnectivityPolicyError("invalid connectivity policy identity")
        machines = value["machines"]
        if set(machines) != {"stack-host", "thin-client", "vps"}:
            raise ConnectivityPolicyError("machine roles are incomplete")
        aliases: set[str] = set()
        for role, machine in machines.items():
            required = {"aliases", "role", "allowed_sources"}
            if role != "vps":
                required.add("windows_user")
            if role == "stack-host":
                required |= {"wsl_distro", "wsl_user"}
            _closed(machine, required, f"machine {role}")
            if not isinstance(machine["aliases"], list) or not machine["aliases"]:
                raise ConnectivityPolicyError("machine aliases are required")
            if any(not isinstance(alias, str) or not NAME.fullmatch(alias) for alias in machine["aliases"]):
                raise ConnectivityPolicyError("invalid machine alias")
            if aliases.intersection(machine["aliases"]):
                raise ConnectivityPolicyError("machine aliases overlap")
            aliases.update(machine["aliases"])
            if not isinstance(machine["allowed_sources"], list) or not machine["allowed_sources"]:
                raise ConnectivityPolicyError("observation sources are required")
        if value["authority_machine"] != "stack-host":
            raise ConnectivityPolicyError("NUCBOX stack host must remain authoritative")
        freshness = value["freshness_seconds"]
        if set(freshness) != AXES or any(type(item) is not int or not 1 <= item <= 86_400 for item in freshness.values()):
            raise ConnectivityPolicyError("freshness policy is invalid")
        resources = value["resources"]
        if set(resources) != {"gateway-8642", "dashboard-9119", "__PRIVATE_TOKEN_02__-mount", "telegram-gateway"}:
            raise ConnectivityPolicyError("topology resources are incomplete")
        for resource_id, resource in resources.items():
            _closed(resource, {"owner", "required_axes", "drift_action"}, f"resource {resource_id}")
            if resource["owner"] not in machines or not isinstance(resource["required_axes"], list):
                raise ConnectivityPolicyError("resource owner or axes are invalid")
            for requirement in resource["required_axes"]:
                _closed(requirement, {"machine", "axis", "expected"}, "axis requirement")
                if requirement["machine"] not in machines or requirement["axis"] not in AXES:
                    raise ConnectivityPolicyError("resource axis is invalid")
                if not isinstance(requirement["expected"], dict) or not requirement["expected"]:
                    raise ConnectivityPolicyError("resource expectation is empty")
        gateway = resources["gateway-8642"]["required_axes"][0]["expected"]
        dashboard = resources["dashboard-9119"]["required_axes"][0]["expected"]
        telegram = resources["telegram-gateway"]
        if gateway.get("binding") != "0.0.0.0:8642":
            raise ConnectivityPolicyError("gateway must remain tailnet reachable")
        if dashboard.get("binding") != "127.0.0.1:9119":
            raise ConnectivityPolicyError("dashboard must remain loopback only")
        if telegram["owner"] != "vps":
            raise ConnectivityPolicyError("VPS must remain Telegram owner")
        changes = value["approval_required_changes"]
        required_changes = {
            "tailscale_acl", "firewall_exposure", "credential", "exit_node",
            "subnet_route", "new_mount", "broad_permission",
            "dashboard_exposure", "service_ownership",
        }
        if not isinstance(changes, list) or set(changes) != required_changes:
            raise ConnectivityPolicyError("approval-required changes are incomplete")
        envelopes = value["repair_envelopes"]
        if set(envelopes) != {"gateway-host-binding"}:
            raise ConnectivityPolicyError("repair envelopes are not closed")
        envelope = _closed(envelopes["gateway-host-binding"], {
            "kind", "authority_machine", "target", "key", "allowed_before",
            "desired", "remote_required", "restart_command",
        }, "gateway repair envelope")
        target = PurePosixPath(envelope["target"])
        if target.is_absolute() or ".." in target.parts or envelope["authority_machine"] != "stack-host":
            raise ConnectivityPolicyError("repair target or authority is unsafe")
        if envelope["kind"] != "env-key" or envelope["key"] != "API_SERVER_HOST":
            raise ConnectivityPolicyError("repair envelope is not exact")
        if envelope["allowed_before"] != ["127.0.0.1", "0.0.0.0"] or envelope["desired"] != "0.0.0.0":
            raise ConnectivityPolicyError("gateway repair values are invalid")
        if envelope["remote_required"] is not True or not isinstance(envelope["restart_command"], list):
            raise ConnectivityPolicyError("gateway repair verification is incomplete")
        return cls(
            enabled=value["enabled"], authority_machine=value["authority_machine"],
            machines=copy.deepcopy(machines), freshness_seconds=dict(freshness),
            resources=copy.deepcopy(resources), approval_required_changes=frozenset(changes),
            repair_envelopes=copy.deepcopy(envelopes), source_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def machine_role(self, alias: str) -> str:
        for role, machine in self.machines.items():
            if alias in machine["aliases"]:
                return role
        raise ConnectivityPolicyError("unknown machine identity")

    def accepts_source(self, role: str, source: str) -> bool:
        return source in self.machines[role]["allowed_sources"]

    def envelope(self, name: str) -> dict[str, Any] | None:
        value = self.repair_envelopes.get(name)
        return copy.deepcopy(value) if value is not None else None
