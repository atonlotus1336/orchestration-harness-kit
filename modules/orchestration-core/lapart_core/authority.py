"""Fail-closed authority decisions for shared-core effects."""

from __future__ import annotations

from .errors import CoreError
from .policy import CorePolicy


DENIED_ACTIONS = {
    "bypass_authority",
    "copy_credentials",
    "inject_core_policy",
    "rewrite_history",
}
EXTERNAL_EFFECTS = {
    "activate",
    "commit",
    "delete",
    "merge",
    "provider_call",
    "push",
    "restart",
    "write_external",
}


def decide_authority(
    *, action: str, resource: str, risk: str, approval_reference: str | None, policy: CorePolicy
) -> dict:
    if not action or not resource or risk not in {"low", "medium", "high", "critical"}:
        raise CoreError("AUTHORITY_REQUEST_INVALID", "Authority request is invalid.")
    rules: list[str] = []
    if action in DENIED_ACTIONS:
        return {"decision": "deny", "approval_reference": None, "rules": ["hard_denial"]}
    if action == "pay_per_token" and not policy.pay_per_token_allowed:
        return {"decision": "deny", "approval_reference": None, "rules": ["subscription_first"]}
    if action == "activate" and resource == "production" and not policy.production_activation_allowed:
        return {"decision": "deny", "approval_reference": None, "rules": ["production_activation_disabled"]}
    if action in EXTERNAL_EFFECTS:
        rules.append("external_effect_approval")
    if risk in {"high", "critical"}:
        rules.append("risk_approval")
    if rules and not approval_reference:
        return {"decision": "ask", "approval_reference": None, "rules": sorted(set(rules))}
    return {
        "decision": "allow",
        "approval_reference": approval_reference,
        "rules": sorted(set(rules)) or ["provider_free_local"],
    }
