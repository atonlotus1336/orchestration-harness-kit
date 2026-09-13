"""Hermes-owned forwarding boundary for Prime capability requests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from prime_bridge.capability_ledger import CapabilityLedger, CapabilityLedgerError
from prime_bridge.capability_policy import CapabilityPolicy, CapabilityPolicyError
from prime_bridge.orchestration_switch import (
    HARNESS_REGISTRY, SwitchControlError, operation_permitted,
)


class CapabilityAdapter(Protocol):
    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any: ...
    def configured(self) -> tuple[str, ...]: ...


class CapabilityLeaseGuard(Protocol):
    """Optional server-owned GER lease validator for Prime capability calls."""

    def authorize(self, *, token: str, task_id: str, actor_id: str, audience: str,
                  tool_id: str, action: str, resource: str, arguments_digest: str,
                  policy_revision: str, budget: dict[str, int | float]) -> None: ...


class CapabilityBrokerError(RuntimeError):
    pass


class CapabilityBroker:
    def __init__(
        self, policy: CapabilityPolicy, ledger: CapabilityLedger,
        adapter: CapabilityAdapter, *, job_ledger: Any | None = None,
        internal_handlers: dict[str, Callable[[str, dict[str, Any], dict[str, str]], Awaitable[Any]]] | None = None,
        lease_guard: CapabilityLeaseGuard | None = None,
        switch_state_path=None,
        operation_guards: dict[str, tuple[str, str]] | None = None,
    ):
        self.policy = policy
        self.ledger = ledger
        self.adapter = adapter
        self.job_ledger = job_ledger
        self.internal_handlers = dict(internal_handlers or {})
        self.lease_guard = lease_guard
        self.switch_state_path = switch_state_path
        self.operation_guards = {
            "memory.search": ("memory", "recall"),
            "rag.search": ("rag", "query"),
            "learning.record": ("learning", "record"),
            "learning.propose": ("learning", "propose"),
            "herdr.reviewer.start": ("herdr-review", "start"),
            **dict(operation_guards or {}),
        }

    def _policy_revision(self) -> str:
        material = {
            "enabled": self.policy.enabled,
            "aliases": {
                alias: {
                    "classification": item.classification, "approval": item.approval,
                    "server": item.server, "tool": item.tool,
                }
                for alias, item in sorted(self.policy.aliases.items())
            },
            "approval_ttl_seconds": self.policy.approval_ttl_seconds,
            "max_argument_bytes": self.policy.max_argument_bytes,
            "max_result_bytes": self.policy.max_result_bytes,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _audit(self, job_id: str, kind: str, data: dict[str, Any]) -> None:
        if self.job_ledger is not None:
            self.job_ledger.note(job_id, kind, data)

    async def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) - {"job_id", "profile", "harness", "alias", "arguments", "request_id", "lease_token"}:
            raise CapabilityBrokerError("invalid capability request")
        job_id, profile, alias = request.get("job_id"), request.get("profile"), request.get("alias")
        if not all(isinstance(value, str) for value in (job_id, profile, alias)):
            raise CapabilityBrokerError("invalid capability request identity")
        harness = request.get("harness")
        if harness is None:
            harness = profile if profile in HARNESS_REGISTRY else "hermes"
        if harness not in HARNESS_REGISTRY:
            raise CapabilityBrokerError("invalid capability request harness")
        guarded = self.operation_guards.get(alias)
        if guarded is not None:
            try:
                if not operation_permitted(
                        guarded[0], harness, guarded[1], self.switch_state_path):
                    raise CapabilityBrokerError(
                        f"{guarded[0]} switch denies {guarded[1]}")
            except SwitchControlError as exc:
                raise CapabilityBrokerError(
                    "orchestration switch state is invalid") from exc
        active_request_id: str | None = None
        try:
            decision = self.policy.decision(alias)
            arguments = self.policy.canonical_arguments(request.get("arguments", {}))
            if self.lease_guard is not None:
                lease_token = request.get("lease_token")
                if not isinstance(lease_token, str) or not lease_token:
                    raise CapabilityBrokerError("capability lease is required")
                self.lease_guard.authorize(
                    token=lease_token, task_id=job_id, actor_id=profile,
                    audience="prime-capability-broker", tool_id=f"{decision.server}.{decision.tool}",
                    action=alias, resource=alias, arguments_digest="sha256:" + arguments.digest,
                    policy_revision=self._policy_revision(),
                    budget={"max_wall_seconds": self.policy.approval_ttl_seconds,
                            "max_tool_calls": 1, "max_cost_usd": 0},
                )
            request_id = request.get("request_id")
            if request_id is None:
                record = self.ledger.create(
                    job_id=job_id, profile=profile, alias=alias,
                    decision=decision, arguments=arguments,
                )
                self._audit(job_id, "capability_requested", {
                    "request_id": record["request_id"], "alias": alias,
                    "classification": decision.classification,
                    "argument_hash": arguments.digest,
                    "approval": decision.approval,
                })
                if record["status"] == "pending":
                    return {
                        "status": "approval_required", "request_id": record["request_id"],
                        "alias": alias, "classification": decision.classification,
                    }
                request_id = record["request_id"]
            elif not isinstance(request_id, str):
                raise CapabilityBrokerError("invalid capability request ID")
            existing = self.ledger.get(request_id)
            active_request_id = request_id
            if (existing["alias"] != alias or
                    existing["target"] != {"server": decision.server, "tool": decision.tool}):
                raise CapabilityBrokerError("approval binding mismatch")
            self.ledger.authorize(
                request_id, profile=profile, job_id=job_id,
                argument_hash=arguments.digest,
            )
            if decision.server in self.internal_handlers:
                receipt_material = {
                    "job_id": job_id, "profile": profile, "request_id": request_id,
                    "alias": alias, "arguments_digest": arguments.digest,
                }
                capability_receipt = hashlib.sha256(
                    json.dumps(receipt_material, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                self._audit(job_id, "capability_authorized", {
                    "request_id": request_id,
                    "alias": alias,
                    "capability_digest": hashlib.sha256(capability_receipt.encode()).hexdigest(),
                })
                result = await self.internal_handlers[decision.server](
                    decision.tool, arguments.value(), {
                        "job_id": job_id, "profile": profile, "harness": harness, "request_id": request_id,
                        "capability_receipt": capability_receipt,
                    },
                )
            else:
                result = await self.adapter.call(decision.server, decision.tool, arguments.value())
            sanitized = self.policy.sanitize_result(result)
            encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            summary = {"status": "ok", "bytes": len(encoded), "result_hash": hashlib.sha256(encoded).hexdigest()}
            self.ledger.complete(request_id, outcome="ok", result_summary=summary)
            self._audit(job_id, "capability_completed", {
                "request_id": request_id, "alias": alias,
                "classification": decision.classification,
                "result_hash": summary["result_hash"], "approval": existing["approval_provenance"]["kind"],
            })
            return {"status": "ok", "request_id": request_id, "result": sanitized}
        except (CapabilityPolicyError, CapabilityLedgerError) as exc:
            raise CapabilityBrokerError(str(exc)) from exc
        except CapabilityBrokerError:
            raise
        except Exception as exc:
            if active_request_id is not None:
                try:
                    self.ledger.complete(active_request_id, outcome="error", result_summary={"status": "error", "type": type(exc).__name__})
                except CapabilityLedgerError:
                    pass
            raise CapabilityBrokerError(f"capability execution failed: {type(exc).__name__}") from exc

    def health(self) -> dict[str, str]:
        try:
            configured = set(self.adapter.configured()) | set(self.internal_handlers)
            targets = {item.server for item in self.policy.aliases.values()}
            retrieval_targets = {item.server for item in self.policy.aliases.values() if item.classification == "retrieval"}
            transport = "configured" if targets & configured else "unavailable"
            retrieval = "configured" if retrieval_targets & configured else "unavailable"
        except Exception:  # noqa: BLE001 - readiness must fail closed for any adapter/config failure
            transport = retrieval = "unavailable"
        return {
            "liveness": "ready", "policy": "ready" if self.policy.enabled else "disabled",
            "mcp_transport": transport, "retrieval": retrieval,
            "remote": "not-probed", "authentication": "host-owned",
        }
