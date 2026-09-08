"""Subscription-first provider and backend admission with exact accounting."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .errors import CoreError
from .policy import CorePolicy


ROUTE_CLASSES = {"provider_free", "subscription", "pay_per_token"}
TASK_SHAPES = {"general", "coding", "computer_use", "long_running_workflow", "domain_workflow"}
USAGE_CONTEXTS = {"internal", "distributable_product"}
AUTH_MODES = {"provider_free", "local", "subscription", "api_key", "workload_identity"}
RETENTION_CLASSES = {"unknown", "ephemeral", "standard", "zero"}
QUALIFICATION_STATES = {"qualified", "stale", "unqualified"}
TERMS_STATES = {"current", "stale", "unverified"}
REQUIREMENT_KEYS = {
    "task_shape", "durability_required", "latency_budget_ms", "usage_context",
    "auth_mode", "retention_class", "compliance_class", "backend_version",
    "require_current_terms",
}
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")


def _version_tuple(value: str) -> tuple[int, ...]:
    if not _VERSION.fullmatch(value):
        raise ValueError("backend version must be a numeric dotted version")
    return tuple(int(item) for item in value.split("."))


def _version_in_range(value: str, minimum: str | None, maximum_exclusive: str | None) -> bool:
    current = _version_tuple(value)
    return (
        (minimum is None or current >= _version_tuple(minimum))
        and (maximum_exclusive is None or current < _version_tuple(maximum_exclusive))
    )


@dataclass(frozen=True)
class ProviderLease:
    """Public, credential-free usage constraints for a provider route."""

    auth_modes: frozenset[str] = frozenset({"provider_free"})
    usage_contexts: frozenset[str] = frozenset({"internal"})
    subscription_delegation_allowed: bool = False
    retention_classes: frozenset[str] = frozenset({"unknown"})
    compliance_classes: frozenset[str] = frozenset({"general"})
    terms_status: str = "unverified"
    terms_reference: str | None = None


@dataclass(frozen=True)
class BackendContract:
    """Deterministic backend-fit and compatibility facts."""

    backend_reference: str = "native"
    task_shapes: frozenset[str] = frozenset({"general"})
    durable_resume: bool = False
    estimated_overhead_ms: int | None = None
    backend_version: str | None = None
    minimum_version: str | None = None
    maximum_version_exclusive: str | None = None
    qualification_status: str = "qualified"
    requalification_triggers: frozenset[str] = frozenset({
        "backend_version_change", "source_hash_change", "terms_status_change",
    })


@dataclass(frozen=True)
class ProviderRoute:
    provider_reference: str
    model_reference: str
    route_class: str
    profiles: frozenset[str]
    lease: ProviderLease = field(default_factory=ProviderLease)
    backend: BackendContract = field(default_factory=BackendContract)


class ProviderRegistry:
    def __init__(self, routes: list[ProviderRoute] | None = None):
        self._routes: list[ProviderRoute] = []
        for route in routes or []:
            self.register(route)

    def register(self, route: ProviderRoute) -> None:
        if (
            not route.provider_reference
            or not route.model_reference
            or route.route_class not in ROUTE_CLASSES
            or not route.profiles
        ):
            raise CoreError("PROVIDER_ROUTE_INVALID", "Provider route is invalid.")
        lease = route.lease
        backend = route.backend
        if (
            not lease.auth_modes or not lease.auth_modes.issubset(AUTH_MODES)
            or not lease.usage_contexts or not lease.usage_contexts.issubset(USAGE_CONTEXTS)
            or type(lease.subscription_delegation_allowed) is not bool
            or not lease.retention_classes or not lease.retention_classes.issubset(RETENTION_CLASSES)
            or not lease.compliance_classes
            or any(not _IDENTIFIER.fullmatch(item) for item in lease.compliance_classes)
            or lease.terms_status not in TERMS_STATES
            or (lease.terms_reference is not None and not lease.terms_reference)
        ):
            raise CoreError("PROVIDER_LEASE_INVALID", "Provider lease is invalid.")
        if (
            not _IDENTIFIER.fullmatch(backend.backend_reference)
            or not backend.task_shapes or not backend.task_shapes.issubset(TASK_SHAPES)
            or type(backend.durable_resume) is not bool
            or (backend.estimated_overhead_ms is not None and (
                type(backend.estimated_overhead_ms) is not int or backend.estimated_overhead_ms < 0
            ))
            or backend.qualification_status not in QUALIFICATION_STATES
            or not backend.requalification_triggers
            or any(not _IDENTIFIER.fullmatch(item) for item in backend.requalification_triggers)
        ):
            raise CoreError("BACKEND_CONTRACT_INVALID", "Backend contract is invalid.")
        try:
            versions = (backend.backend_version, backend.minimum_version, backend.maximum_version_exclusive)
            if any(value is not None and not _VERSION.fullmatch(value) for value in versions):
                raise ValueError
            if backend.backend_version is not None and not _version_in_range(
                backend.backend_version, backend.minimum_version, backend.maximum_version_exclusive,
            ):
                raise ValueError
            if backend.minimum_version is not None and backend.maximum_version_exclusive is not None \
                    and _version_tuple(backend.minimum_version) >= _version_tuple(backend.maximum_version_exclusive):
                raise ValueError
        except ValueError as exc:
            raise CoreError("BACKEND_VERSION_INVALID", "Backend version contract is invalid.") from exc
        self._routes.append(route)

    @staticmethod
    def _requirements(value: dict[str, Any] | None) -> dict[str, Any]:
        requirements = dict(value or {})
        if set(requirements) - REQUIREMENT_KEYS:
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Provider requirement keys are not closed.")
        if "task_shape" in requirements and requirements["task_shape"] not in TASK_SHAPES:
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Task shape is invalid.")
        if "durability_required" in requirements and type(requirements["durability_required"]) is not bool:
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Durability requirement is invalid.")
        latency = requirements.get("latency_budget_ms")
        if latency is not None and (type(latency) is not int or latency < 0):
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Latency budget is invalid.")
        if "usage_context" in requirements and requirements["usage_context"] not in USAGE_CONTEXTS:
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Usage context is invalid.")
        if "auth_mode" in requirements and requirements["auth_mode"] not in AUTH_MODES:
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Authentication mode is invalid.")
        if "retention_class" in requirements and requirements["retention_class"] not in RETENTION_CLASSES:
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Retention requirement is invalid.")
        compliance = requirements.get("compliance_class")
        if compliance is not None and (not isinstance(compliance, str) or not _IDENTIFIER.fullmatch(compliance)):
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Compliance requirement is invalid.")
        version = requirements.get("backend_version")
        if version is not None and (not isinstance(version, str) or not _VERSION.fullmatch(version)):
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Backend version is invalid.")
        if "require_current_terms" in requirements and type(requirements["require_current_terms"]) is not bool:
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Terms requirement is invalid.")
        return requirements

    @staticmethod
    def _fits(route: ProviderRoute, requirements: dict[str, Any]) -> bool:
        lease = route.lease
        backend = route.backend
        task_shape = requirements.get("task_shape")
        if task_shape and task_shape not in backend.task_shapes and "general" not in backend.task_shapes:
            return False
        if requirements.get("durability_required") and not backend.durable_resume:
            return False
        latency = requirements.get("latency_budget_ms")
        if latency is not None and (
            backend.estimated_overhead_ms is None or backend.estimated_overhead_ms > latency
        ):
            return False
        usage_context = requirements.get("usage_context")
        if usage_context and usage_context not in lease.usage_contexts:
            return False
        auth_mode = requirements.get("auth_mode")
        if auth_mode and auth_mode not in lease.auth_modes:
            return False
        if (
            usage_context == "distributable_product"
            and auth_mode == "subscription"
            and not lease.subscription_delegation_allowed
        ):
            return False
        retention = requirements.get("retention_class")
        if retention and retention not in lease.retention_classes:
            return False
        compliance = requirements.get("compliance_class")
        if compliance and compliance not in lease.compliance_classes:
            return False
        if requirements.get("require_current_terms") and lease.terms_status != "current":
            return False
        version = requirements.get("backend_version") or backend.backend_version
        if backend.qualification_status != "qualified":
            return False
        if version is not None and not _version_in_range(
            version, backend.minimum_version, backend.maximum_version_exclusive,
        ):
            return False
        return True

    def resolve(
        self,
        *,
        profile: str,
        policy: CorePolicy,
        allowed_route_classes: list[str] | None = None,
        approval_reference: str | None = None,
        requirements: dict[str, Any] | None = None,
    ) -> dict:
        allowed = set(allowed_route_classes or ["provider_free", "subscription"])
        if not allowed or not allowed.issubset(ROUTE_CLASSES):
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Provider constraints are invalid.")
        if "pay_per_token" in allowed and (
            not policy.pay_per_token_allowed or not approval_reference
        ):
            allowed.remove("pay_per_token")
        normalized_requirements = self._requirements(requirements)
        preference = ["provider_free", "subscription", "pay_per_token"]
        candidates = [
            route
            for route in self._routes
            if profile in route.profiles and route.route_class in allowed
            and self._fits(route, normalized_requirements)
        ]
        candidates.sort(
            key=lambda route: (
                preference.index(route.route_class),
                0 if normalized_requirements.get("task_shape") in route.backend.task_shapes else 1,
                route.backend.estimated_overhead_ms if route.backend.estimated_overhead_ms is not None else 2**31,
                route.provider_reference,
                route.model_reference,
            )
        )
        if not candidates:
            scoped = [route for route in self._routes if profile in route.profiles and route.route_class in allowed]
            code = "PROVIDER_CONSTRAINT_UNSATISFIED" if scoped else "PROVIDER_ROUTE_UNAVAILABLE"
            raise CoreError(code, "No provider route satisfies the required lease and backend constraints.")
        selected = candidates[0]
        return {
            "provider_reference": selected.provider_reference,
            "model_reference": selected.model_reference,
            "route_class": selected.route_class,
            "rationale": "subscription_first" if policy.subscription_first else "policy_order",
            "backend_fit": {
                "status": "FIT",
                "backend_reference": selected.backend.backend_reference,
                "task_shape": normalized_requirements.get("task_shape", "unspecified"),
                "durable_resume": selected.backend.durable_resume,
                "estimated_overhead_ms": selected.backend.estimated_overhead_ms,
            },
            "lease": {
                "auth_modes": sorted(selected.lease.auth_modes),
                "usage_contexts": sorted(selected.lease.usage_contexts),
                "subscription_delegation_allowed": selected.lease.subscription_delegation_allowed,
                "retention_classes": sorted(selected.lease.retention_classes),
                "compliance_classes": sorted(selected.lease.compliance_classes),
                "terms_status": selected.lease.terms_status,
                "terms_reference": selected.lease.terms_reference,
            },
            "qualification": {
                "status": selected.backend.qualification_status,
                "backend_version": normalized_requirements.get("backend_version") or selected.backend.backend_version,
                "minimum_version": selected.backend.minimum_version,
                "maximum_version_exclusive": selected.backend.maximum_version_exclusive,
                "requalification_triggers": sorted(selected.backend.requalification_triggers),
            },
        }


class ProviderAccounting:
    def __init__(self):
        self._calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._route_class = "provider_free"

    def record(self, *, input_tokens: int, output_tokens: int, route_class: str) -> None:
        if route_class not in {"subscription", "pay_per_token"}:
            raise CoreError("ACCOUNTING_INVALID", "A provider call needs a provider route.")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (input_tokens, output_tokens)):
            raise CoreError("ACCOUNTING_INVALID", "Token counts must be nonnegative integers.")
        self._calls += 1
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._route_class = route_class

    def snapshot(self) -> dict:
        return {
            "provider_calls": self._calls,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "route_class": self._route_class,
            "pay_per_token": self._route_class == "pay_per_token",
        }
