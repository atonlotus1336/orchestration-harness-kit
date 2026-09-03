"""Universal bridge from harness-local events to the shared orchestration core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .adapter import AdapterRequest, HarnessAdapter
from .manifest import HarnessManifest, ManifestError
from .negotiation import NegotiatedHarness, negotiate_harness


class CorePort(Protocol):
    policy: Any
    handlers: dict[str, Any]

    def handle(self, envelope: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BridgeResult:
    local_response: Any
    envelope: dict[str, Any]
    provider_accounting: dict[str, Any]
    context_attribution: dict[str, Any] | None


class UniversalHarnessBridge:
    """Creates canonical envelopes while keeping adapters translation-only."""

    CORE_CAPABILITIES = {"streaming", "tools", "skills", "mcp", "memory", "retrieval"}

    def __init__(self, *, manifest: HarnessManifest, adapter: HarnessAdapter, core: CorePort):
        if adapter.manifest.fingerprint != manifest.fingerprint:
            raise ManifestError("MANIFEST_MISMATCH", "adapter and bridge manifests differ")
        self.manifest = manifest
        self.adapter = adapter
        self.core = core
        self.negotiated: NegotiatedHarness = negotiate_harness(
            manifest,
            core_protocol_versions=tuple(core.policy.protocol_versions),
            core_operations=set(core.handlers),
            core_capabilities=self.CORE_CAPABILITIES,
        )
        self._sequence = 0

    def dispatch(self, local_event: Any) -> BridgeResult:
        translated = self.adapter.translate_request(local_event)
        return self._dispatch_request(translated)

    def initialize(self, *, session_id: str) -> BridgeResult:
        self.adapter.initialize(self.manifest.config_reference, self.manifest.data_reference)
        return self._dispatch_request(
            AdapterRequest(
                operation="lifecycle.initialize",
                payload={"manifest": self.manifest.name, "supported_versions": list(self.manifest.protocol_versions)},
                session_id=session_id,
                session_state="new",
            )
        )

    def shutdown(self, *, session_id: str, reason: str) -> BridgeResult:
        result = self._dispatch_request(
            AdapterRequest(
                operation="lifecycle.shutdown",
                payload={"reason": reason},
                session_id=session_id,
                session_state="active",
            )
        )
        self.adapter.shutdown(reason)
        return result

    def _dispatch_request(self, translated: AdapterRequest) -> BridgeResult:
        if translated.operation not in self.negotiated.operations:
            raise ManifestError("OPERATION_NOT_NEGOTIATED", "adapter requested an unavailable operation")
        self._sequence += 1
        envelope: dict[str, Any] = {
            "protocol_version": self.negotiated.protocol_version,
            "message_id": f"bridge-{self._sequence:08d}",
            "kind": "request",
            "operation": translated.operation,
            "harness": {
                "name": self.manifest.name,
                "version": self.manifest.harness_version,
                "adapter_version": str(self.manifest.adapter_sdk_version),
            },
            "session": {"id": translated.session_id, "state": translated.session_state},
            "payload": dict(translated.payload),
            "extensions": {"manifest_fingerprint": self.manifest.fingerprint},
        }
        if self.manifest.authentication is not None:
            envelope["authentication"] = dict(self.manifest.authentication)
        response = self.core.handle(envelope)
        accounting = response.get("provider_accounting")
        if not isinstance(accounting, dict):
            raise RuntimeError("shared core omitted provider accounting")
        local_response = self.adapter.translate_response(response)
        return BridgeResult(
            local_response=local_response,
            envelope=response,
            provider_accounting=dict(accounting),
            context_attribution=response.get("context_attribution"),
        )
