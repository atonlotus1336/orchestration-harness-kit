"""Structured multi-axis production readiness for Prime rollout."""

from __future__ import annotations

from typing import Any, Mapping


class OperationsError(RuntimeError):
    pass


REQUIRED_AXES = frozenset({
    "bridge_liveness", "runtime_compatibility", "capability_readiness",
    "model_auth_readiness", "remote_client_reachability", "profile_rollout",
})


class RolloutDashboard:
    def evaluate(self, axes: Mapping[str, Any], *, emergency_disabled: bool) -> dict[str, Any]:
        if not isinstance(axes, Mapping) or set(axes) != REQUIRED_AXES:
            raise OperationsError("readiness axes are not closed")
        public = {}
        failures = []
        for name in sorted(REQUIRED_AXES):
            value = axes[name]
            if not isinstance(value, Mapping) or set(value) != {"status", "reason"}:
                raise OperationsError(f"invalid readiness axis: {name}")
            if value["status"] not in {"PASS", "FAIL", "UNVERIFIED", "DEFERRED"}:
                raise OperationsError(f"invalid readiness status: {name}")
            if not isinstance(value["reason"], str) or not value["reason"]:
                raise OperationsError(f"readiness reason required: {name}")
            public[name] = {"status": value["status"], "reason": value["reason"]}
            if value["status"] != "PASS":
                failures.append(name)
        status = "DISABLED" if emergency_disabled else ("READY" if not failures else "DEGRADED")
        return {
            "status": status,
            "axes": public,
            "unready_axes": failures,
            "emergency_disabled": emergency_disabled,
            "native_hermes": "AVAILABLE",
            "one_backend": True,
        }
