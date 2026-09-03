"""Protocol and operation negotiation between a manifest and shared core."""

from __future__ import annotations

from dataclasses import dataclass

from installer.lib.versioning import negotiate_protocol

from .manifest import HarnessManifest, ManifestError


@dataclass(frozen=True)
class NegotiatedHarness:
    protocol_version: int
    operations: frozenset[str]
    capabilities: frozenset[str]
    degraded: bool
    manifest_fingerprint: str


def negotiate_harness(
    manifest: HarnessManifest,
    *,
    core_protocol_versions: tuple[int, ...],
    core_operations: set[str],
    core_capabilities: set[str],
) -> NegotiatedHarness:
    selected_version = negotiate_protocol(manifest.protocol_versions, core_protocol_versions)
    operations = manifest.operations.intersection(core_operations)
    if not operations:
        raise ManifestError("NO_MUTUAL_OPERATIONS", "manifest and core share no operations")
    requested_capabilities = {name for name, enabled in manifest.capabilities.items() if enabled}
    capabilities = requested_capabilities.intersection(core_capabilities)
    return NegotiatedHarness(
        protocol_version=selected_version,
        operations=frozenset(operations),
        capabilities=frozenset(capabilities),
        degraded=operations != manifest.operations or capabilities != requested_capabilities,
        manifest_fingerprint=manifest.fingerprint,
    )
