"""One lifecycle-aware control surface for the optional Orca provider."""
from __future__ import annotations

from typing import Any

from .harness_registry import UNIVERSAL_HARNESS_REGISTRY
from .orchestration_switch import OrchestrationSwitch


class OrcaControlError(RuntimeError):
    pass


class OrcaControl:
    """Coordinate switch intent with worker drain and provider visibility."""

    def __init__(self, switch: OrchestrationSwitch, *, provider=None,
                 provider_registered: bool | None = None):
        self.switch = switch
        self.provider = provider
        self.provider_registered = (
            provider is not None if provider_registered is None
            else provider_registered
        )

    def status(self, harnesses=None) -> dict[str, Any]:
        selected = tuple(harnesses or UNIVERSAL_HARNESS_REGISTRY)
        switch_status = self.switch.status(feature="orca", harnesses=selected)
        if self.provider is None:
            runtime = {
                "provider_registered": self.provider_registered,
                "runtime_ready": "NOT_CONNECTED",
                "active_workers": None,
            }
        else:
            runtime = dict(self.provider.status(selected))
            runtime["provider_registered"] = True
        return {**switch_status, "runtime": runtime}

    def set(self, mode: str, harnesses) -> dict[str, Any]:
        selected = tuple(harnesses)
        if mode != "off":
            result = self.switch.set("orca", mode, selected)
            result["runtime"] = self.status(selected)["runtime"]
            return result

        # Off is a verified terminal state, not merely a paper permission bit.
        quiesced = self.switch.set("orca", "quiesce", selected)
        if quiesced.get("result") not in {"ACTIVE", "PARTIAL"}:
            return {
                **quiesced, "result": "QUIESCE_FAILED",
                "runtime": self.status(selected)["runtime"],
            }
        if self.provider is None:
            return {
                "changed": True, "result": "DRAIN_REQUIRED",
                "after": self.status(selected),
                "reason": "ORCA_RUNTIME_ADAPTER_REQUIRED",
            }
        drain = dict(self.provider.drain(selected))
        if drain.get("remaining", 1) != 0:
            return {
                "changed": True, "result": "DRAIN_INCOMPLETE",
                "drain": drain, "after": self.status(selected),
            }
        disabled = self.switch.set(
            "orca", "off", selected, verified_orca_off=True,
        )
        disabled["drain"] = drain
        disabled["runtime"] = self.status(selected)["runtime"]
        return disabled


__all__ = ["OrcaControl", "OrcaControlError"]
