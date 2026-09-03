"""Closed, credential-reference-only harness manifest loader."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping


TOP_LEVEL = {
    "schema",
    "name",
    "display_name",
    "harness_version",
    "adapter",
    "protocol_versions",
    "operations",
    "capabilities",
    "configuration",
    "lifecycle",
}
CAPABILITIES = {"streaming", "tools", "skills", "mcp", "memory", "retrieval"}
FORBIDDEN_KEYS = {
    "api_key",
    "core_policy",
    "credential",
    "password",
    "policy_override",
    "provider_policy",
    "routing_policy",
    "secret",
    "token",
}
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ManifestError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def _closed(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ManifestError("MANIFEST_INVALID", f"{label} keys are not closed")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError("MANIFEST_INVALID", f"{label} must be a nonempty string")
    return value


def _forbidden_paths(value: Any, path: str = "manifest") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.casefold() in FORBIDDEN_KEYS:
                found.append(child_path)
            found.extend(_forbidden_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_paths(child, f"{path}[{index}]"))
    return found


def _portable_reference(value: Any, label: str) -> str:
    reference = _nonempty(value, label)
    private_home_prefixes = ("/" + "home/", "/" + "Users/")
    if "\x00" in reference or re.match(r"^[A-Za-z]:[\\/]", reference) or reference.startswith(private_home_prefixes):
        raise ManifestError("MACHINE_PATH_DENIED", f"{label} contains a machine-specific path")
    return reference


@dataclass(frozen=True)
class HarnessManifest:
    name: str
    display_name: str
    harness_version: str
    adapter_language: str
    adapter_entrypoint: str
    adapter_class: str
    adapter_sdk_version: int
    protocol_versions: tuple[int, ...]
    operations: frozenset[str]
    capabilities: Mapping[str, bool]
    config_reference: str
    data_reference: str
    authentication: Mapping[str, str] | None
    lifecycle: Mapping[str, str]
    fingerprint: str

    @classmethod
    def load(cls, path: Path, *, catalog_path: Path) -> "HarnessManifest":
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestError("MANIFEST_UNAVAILABLE", "manifest or operation catalog is unavailable") from exc
        _closed(value, TOP_LEVEL, "manifest")
        if value["schema"] != "lapart.harness-manifest.v2":
            raise ManifestError("MANIFEST_INVALID", "manifest schema is unsupported")
        forbidden = _forbidden_paths(value)
        if forbidden:
            raise ManifestError("POLICY_OR_CREDENTIAL_DENIED", f"forbidden fields: {', '.join(forbidden)}")
        name = _nonempty(value["name"], "name")
        if not NAME_RE.fullmatch(name):
            raise ManifestError("MANIFEST_INVALID", "name is not a portable harness identifier")

        adapter = _closed(value["adapter"], {"language", "entrypoint", "class", "sdk_version"}, "adapter")
        if adapter["language"] != "python" or adapter["sdk_version"] != 2 or not CLASS_RE.fullmatch(str(adapter["class"])):
            raise ManifestError("ADAPTER_INVALID", "adapter language, class, or SDK version is invalid")
        entrypoint = _nonempty(adapter["entrypoint"], "adapter.entrypoint")
        posix = PurePosixPath(entrypoint)
        if posix.is_absolute() or ".." in posix.parts or "\\" in entrypoint or posix.suffix != ".py":
            raise ManifestError("ADAPTER_PATH_INVALID", "adapter entrypoint must be a relative portable Python path")

        versions = value["protocol_versions"]
        if not isinstance(versions, list) or not versions or len(versions) != len(set(versions)) or any(type(item) is not int or item not in {1, 2} for item in versions):
            raise ManifestError("PROTOCOL_VERSIONS_INVALID", "protocol versions must be a unique subset of 1 and 2")

        catalog_operations = {
            row["name"] for row in catalog.get("operations", [])
            if isinstance(row, dict) and isinstance(row.get("name"), str)
        }
        if not catalog_operations:
            raise ManifestError("CATALOG_INVALID", "operation catalog is empty")
        operation_config = _closed(value["operations"], {"mode", "include", "exclude"}, "operations")
        include = operation_config["include"]
        exclude = operation_config["exclude"]
        if not all(isinstance(rows, list) and len(rows) == len(set(rows)) and all(isinstance(item, str) for item in rows) for rows in (include, exclude)):
            raise ManifestError("OPERATIONS_INVALID", "operation include/exclude lists are invalid")
        unknown = (set(include) | set(exclude)) - catalog_operations
        if unknown:
            raise ManifestError("OPERATIONS_INVALID", f"unknown operations: {', '.join(sorted(unknown))}")
        if operation_config["mode"] == "all" and include:
            raise ManifestError("OPERATIONS_INVALID", "all mode requires an empty include list")
        if operation_config["mode"] == "subset" and not include:
            raise ManifestError("OPERATIONS_INVALID", "subset mode requires included operations")
        if operation_config["mode"] not in {"all", "subset"}:
            raise ManifestError("OPERATIONS_INVALID", "operation mode is invalid")
        selected = (catalog_operations if operation_config["mode"] == "all" else set(include)) - set(exclude)
        if not selected:
            raise ManifestError("OPERATIONS_INVALID", "manifest exposes no operations")

        capabilities = _closed(value["capabilities"], CAPABILITIES, "capabilities")
        if any(type(item) is not bool for item in capabilities.values()):
            raise ManifestError("CAPABILITIES_INVALID", "capability values must be booleans")

        configuration = _closed(value["configuration"], {"config_reference", "data_reference", "authentication"}, "configuration")
        authentication = configuration["authentication"]
        normalized_auth = None
        if authentication is not None:
            authentication = _closed(authentication, {"kind", "reference"}, "authentication")
            if authentication["kind"] not in {"environment", "secret_store", "managed_identity"}:
                raise ManifestError("AUTHENTICATION_INVALID", "authentication kind is invalid")
            normalized_auth = MappingProxyType({
                "kind": authentication["kind"],
                "reference": _portable_reference(authentication["reference"], "authentication.reference"),
            })

        lifecycle = _closed(value["lifecycle"], {"initialize", "shutdown", "readiness", "rollback"}, "lifecycle")
        expected_lifecycle = {
            "initialize": "initialize",
            "shutdown": "shutdown",
            "readiness": "readiness_signal",
            "rollback": "rollback_signal",
        }
        if lifecycle != expected_lifecycle:
            raise ManifestError("LIFECYCLE_INVALID", "lifecycle methods must use the stable adapter interface")
        return cls(
            name=name,
            display_name=_nonempty(value["display_name"], "display_name"),
            harness_version=_nonempty(value["harness_version"], "harness_version"),
            adapter_language="python",
            adapter_entrypoint=entrypoint,
            adapter_class=adapter["class"],
            adapter_sdk_version=2,
            protocol_versions=tuple(sorted(versions)),
            operations=frozenset(selected),
            capabilities=MappingProxyType(dict(capabilities)),
            config_reference=_portable_reference(configuration["config_reference"], "configuration.config_reference"),
            data_reference=_portable_reference(configuration["data_reference"], "configuration.data_reference"),
            authentication=normalized_auth,
            lifecycle=MappingProxyType(dict(lifecycle)),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        )
