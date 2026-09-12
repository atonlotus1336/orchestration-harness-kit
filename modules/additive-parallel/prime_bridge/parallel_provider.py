"""Lazy, removable registration for parallel worker providers.

The registry is deliberately provider-neutral.  Prime can discover a provider,
advertise only its currently permitted capabilities, and construct its handler
without importing provider implementation modules at process import time.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Protocol, runtime_checkable

from .harness_registry import UNIVERSAL_HARNESS_REGISTRY
from .orchestration_switch import operation_permitted


NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,255}\Z")
SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


class ParallelProviderError(ValueError):
    pass


@runtime_checkable
class ParallelWorkerProvider(Protocol):
    """Minimum lifecycle contract shared by router and evaluator consumers."""

    provider_name: str

    def status(self, harnesses=None) -> Mapping[str, Any]: ...

    def active_workers(self, harnesses=None) -> list[Mapping[str, Any]]: ...

    def drain(self, harnesses) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CapabilitySpec:
    alias: str
    server: str
    tool: str
    operation: str


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    feature: str
    module: str
    factory: str
    capabilities: tuple[CapabilitySpec, ...]
    consumers: tuple[str, ...]
    options: Mapping[str, Any]


class ParallelProviderCatalog:
    """Validated provider descriptors with lazy factory loading."""

    def __init__(self, specs: Mapping[str, ProviderSpec], *, source: Path | None = None):
        self.specs = dict(specs)
        self.source = source

    @classmethod
    def load(cls, path: Path | str) -> "ParallelProviderCatalog":
        source = Path(path)
        if not source.exists():
            return cls({}, source=source)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ParallelProviderError("parallel provider catalog is invalid") from exc
        if (not isinstance(value, dict)
                or set(value) != {"schema_version", "providers"}
                or value["schema_version"] != "1.0"
                or not isinstance(value["providers"], dict)):
            raise ParallelProviderError("parallel provider catalog is invalid")
        specs: dict[str, ProviderSpec] = {}
        aliases: set[str] = set()
        servers: set[str] = set()
        for name, item in value["providers"].items():
            if (not isinstance(name, str) or NAME.fullmatch(name) is None
                    or not isinstance(item, dict)
                    or set(item) != {"feature", "module", "factory", "capabilities", "consumers", "options"}
                    or not all(isinstance(item[key], str) for key in ("feature", "module", "factory"))
                    or MODULE.fullmatch(item["module"]) is None
                    or SYMBOL.fullmatch(item["factory"]) is None
                    or not isinstance(item["capabilities"], list)
                    or not isinstance(item["consumers"], list)
                    or not item["consumers"]
                    or len(item["consumers"]) != len(set(item["consumers"]))
                    or set(item["consumers"]) - {"router", "evaluator"}
                    or not isinstance(item["options"], dict)):
                raise ParallelProviderError("parallel provider catalog is invalid")
            capabilities = []
            for capability in item["capabilities"]:
                if (not isinstance(capability, dict)
                        or set(capability) != {"alias", "server", "tool", "operation"}
                        or not all(isinstance(capability[key], str) and capability[key]
                                   for key in capability)):
                    raise ParallelProviderError("parallel provider capability is invalid")
                parsed = CapabilitySpec(**capability)
                if parsed.alias in aliases or parsed.server in servers:
                    raise ParallelProviderError("parallel provider capability is duplicated")
                aliases.add(parsed.alias)
                servers.add(parsed.server)
                capabilities.append(parsed)
            specs[name] = ProviderSpec(
                name=name, feature=item["feature"], module=item["module"],
                factory=item["factory"], capabilities=tuple(capabilities),
                consumers=tuple(item["consumers"]), options=dict(item["options"]),
            )
        return cls(specs, source=source)

    def registered_aliases(self, consumer: str | None = None) -> tuple[str, ...]:
        return tuple(
            capability.alias
            for spec in self.specs.values()
            if consumer is None or consumer in spec.consumers
            for capability in spec.capabilities
        )

    def guard_map(self) -> dict[str, tuple[str, str]]:
        return {
            capability.alias: (spec.feature, capability.operation)
            for spec in self.specs.values()
            for capability in spec.capabilities
        }

    def active_aliases(self, *, switch_path=None, consumer: str = "router") -> tuple[str, ...]:
        aliases = []
        if consumer not in {"router", "evaluator"}:
            raise ParallelProviderError("parallel provider consumer is invalid")
        allowed = {
            capability.alias
            for spec in self.specs.values() if consumer in spec.consumers
            for capability in spec.capabilities
        }
        for harness in UNIVERSAL_HARNESS_REGISTRY:
            aliases.extend(
                alias for alias in self.aliases_for(harness, switch_path=switch_path)
                if alias in allowed
            )
        return tuple(dict.fromkeys(aliases))

    def aliases_for(self, harness: str, *, switch_path=None) -> tuple[str, ...]:
        aliases = []
        for spec in self.specs.values():
            for capability in spec.capabilities:
                if operation_permitted(
                        spec.feature, harness, capability.operation, switch_path):
                    aliases.append(capability.alias)
        return tuple(aliases)

    def build_service(self, name: str, *, ledger, allowed_roots, root: Path,
                      switch_path=None):
        try:
            spec = self.specs[name]
        except KeyError as exc:
            raise ParallelProviderError("parallel provider is not registered") from exc
        try:
            module = importlib.import_module(spec.module)
            factory = getattr(module, spec.factory)
            return factory(
                ledger=ledger, allowed_roots=allowed_roots, root=root,
                switch_path=switch_path, options=dict(spec.options),
            )
        except (ImportError, AttributeError, OSError, TypeError, ValueError) as exc:
            raise ParallelProviderError("parallel provider is unavailable") from exc

    def build_handlers(self, *, ledger, allowed_roots, root: Path,
                       switch_path=None, consumer: str = "router") -> dict[str, Any]:
        """Import and register only catalogued providers.

        Deleting a descriptor cleanly unregisters the corresponding handler on
        the next service construction; a missing provider module is a disabled
        optional capability rather than a Prime startup failure.
        """
        handlers: dict[str, Any] = {}
        active = set(self.active_aliases(
            switch_path=switch_path, consumer=consumer,
        ))
        for spec in self.specs.values():
            if not any(capability.alias in active for capability in spec.capabilities):
                continue
            try:
                service = self.build_service(
                    spec.name, ledger=ledger, allowed_roots=allowed_roots,
                    root=root, switch_path=switch_path,
                )
            except ParallelProviderError:
                continue
            for capability in spec.capabilities:
                handlers[capability.server] = service.call
        return handlers


__all__ = [
    "CapabilitySpec", "ParallelProviderCatalog", "ParallelProviderError",
    "ParallelWorkerProvider", "ProviderSpec",
]
