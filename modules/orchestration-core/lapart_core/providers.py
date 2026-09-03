"""Subscription-first provider resolution and exact call accounting."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import CoreError
from .policy import CorePolicy


ROUTE_CLASSES = {"provider_free", "subscription", "pay_per_token"}


@dataclass(frozen=True)
class ProviderRoute:
    provider_reference: str
    model_reference: str
    route_class: str
    profiles: frozenset[str]


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
        self._routes.append(route)

    def resolve(
        self,
        *,
        profile: str,
        policy: CorePolicy,
        allowed_route_classes: list[str] | None = None,
        approval_reference: str | None = None,
    ) -> dict:
        allowed = set(allowed_route_classes or ["provider_free", "subscription"])
        if not allowed or not allowed.issubset(ROUTE_CLASSES):
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Provider constraints are invalid.")
        if "pay_per_token" in allowed and (
            not policy.pay_per_token_allowed or not approval_reference
        ):
            allowed.remove("pay_per_token")
        preference = ["provider_free", "subscription", "pay_per_token"]
        candidates = [
            route
            for route in self._routes
            if profile in route.profiles and route.route_class in allowed
        ]
        candidates.sort(
            key=lambda route: (
                preference.index(route.route_class),
                route.provider_reference,
                route.model_reference,
            )
        )
        if not candidates:
            raise CoreError("PROVIDER_ROUTE_UNAVAILABLE", "No approved provider route is available.")
        selected = candidates[0]
        return {
            "provider_reference": selected.provider_reference,
            "model_reference": selected.model_reference,
            "route_class": selected.route_class,
            "rationale": "subscription_first" if policy.subscription_first else "policy_order",
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
