"""Protocol-v2 operation processor shared by every harness adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from installer.lib.versioning import IncompatibleProtocolError, negotiate_protocol

from .authority import decide_authority
from .capabilities import CapabilityRegistry
from .context import select_context
from .projection import project_tools
from .errors import CoreError
from .policy import CorePolicy
from .providers import ProviderAccounting, ProviderRegistry
from .routing import select_prime_lane
from .state import CoreState


FORBIDDEN_POLICY_KEYS = {"core_policy", "policy", "policy_override", "routing_policy"}
FORBIDDEN_CREDENTIAL_KEYS = {
    "api_key",
    "credential",
    "credential_value",
    "password",
    "secret",
    "token",
}
REQUEST_KEYS = {
    "protocol_version",
    "message_id",
    "correlation_id",
    "kind",
    "operation",
    "harness",
    "session",
    "authentication",
    "payload",
    "extensions",
}
SESSION_STATES = {"new", "active", "detached", "closed", "failed"}
TELEMETRY_METRIC_KEYS = {"latency_ms", "stage_timings_ms"}
TELEMETRY_STAGES = {
    "adapter", "routing", "provider_wait", "tool_execution", "handoff",
    "evidence_collection", "persistence",
}


class OrchestrationCore:
    """Deterministic, adapter-independent processor for all catalog operations."""

    def __init__(
        self,
        *,
        policy: CorePolicy,
        catalog_path: Path,
        capabilities: CapabilityRegistry | None = None,
        providers: ProviderRegistry | None = None,
        state: CoreState | None = None,
        tool_handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
        retrieval_sources: list[dict[str, str]] | None = None,
    ):
        self.policy = policy
        self.capabilities = capabilities or CapabilityRegistry()
        self.providers = providers or ProviderRegistry()
        self.state = state or CoreState()
        self.accounting = ProviderAccounting()
        self.tool_handlers = dict(tool_handlers or {})
        self.retrieval_sources = list(retrieval_sources or [])
        self.context_selections: dict[str, dict[str, Any]] = {}
        self.omp_jobs: dict[str, dict[str, Any]] = {}
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CoreError("CATALOG_UNAVAILABLE", "Operation catalog is unavailable.") from exc
        entries = catalog.get("operations") if isinstance(catalog, dict) else None
        if not isinstance(entries, list):
            raise CoreError("CATALOG_INVALID", "Operation catalog is invalid.")
        self.operations = {
            row["name"]: row
            for row in entries
            if isinstance(row, dict) and isinstance(row.get("name"), str)
        }
        self.handlers = {
            "lifecycle.initialize": self._lifecycle_initialize,
            "lifecycle.shutdown": self._lifecycle_shutdown,
            "capabilities.negotiate": self._capabilities_negotiate,
            "session.create": self._session_create,
            "session.continue": self._session_continue,
            "session.detach": self._session_detach,
            "session.reattach": self._session_reattach,
            "message.submit": self._message_submit,
            "message.stream": self._message_stream,
            "mcp.discover": lambda p: self._discover("mcp", "servers", p),
            "mcp.connect": self._mcp_connect,
            "tool.discover": lambda p: self._discover("tool", "tools", p),
            "tool.invoke": self._tool_invoke,
            "skill.discover": lambda p: self._discover("skill", "skills", p),
            "skill.load": self._skill_load,
            "memory.read": self._memory_read,
            "memory.write": self._memory_write,
            "retrieval.query": self._retrieval_query,
            "routing.prime.select": self._routing_select,
            "execution.omp.request": self._omp_request,
            "execution.omp.patch": self._omp_patch,
            "authority.decide": self._authority_decide,
            "context.attribute": self._context_attribute,
            "optimization.plan": self._optimization_plan,
            "provider.resolve": self._provider_resolve,
            "provider.account": self._provider_account,
            "telemetry.report": self._telemetry_report,
            "health.readiness": self._health_readiness,
            "activation.transition": self._activation_transition,
            "rollback.execute": self._rollback_execute,
            "verification.run": self._verification_run,
        }
        if set(self.handlers) != set(self.operations):
            raise CoreError("HANDLER_COVERAGE_INCOMPLETE", "Core handler coverage differs from the catalog.")

    @classmethod
    def from_module(cls, module_root: Path, **kwargs: Any) -> "OrchestrationCore":
        module_root = module_root.resolve()
        return cls(
            policy=CorePolicy.load(module_root / "core-policy.json"),
            catalog_path=module_root.parent / "orchestration-protocol" / "operation-catalog.json",
            **kwargs,
        )

    def handle(self, envelope: dict[str, Any]) -> dict[str, Any]:
        operation = envelope.get("operation") if isinstance(envelope, dict) else "unknown"
        try:
            payload = self._validate_request(envelope)
            if operation == "message.submit":
                result = self._message_submit(payload, envelope["session"]["id"])
            else:
                result = self.handlers[operation](payload)
            return self._response(envelope, result)
        except IncompatibleProtocolError as exc:
            error = CoreError(
                "INCOMPATIBLE_PROTOCOL",
                "No mutually supported orchestration protocol version.",
                details=exc.as_dict(),
            )
        except CoreError as exc:
            error = exc
        except (KeyError, TypeError, ValueError) as exc:
            error = CoreError("REQUEST_INVALID", "The operation request is invalid.", details={"type": type(exc).__name__})
        return self._error(envelope if isinstance(envelope, dict) else {}, operation, error)

    def _validate_request(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(envelope, dict):
            raise CoreError("ENVELOPE_INVALID", "Envelope must be an object.")
        required = {"protocol_version", "message_id", "kind", "operation", "harness", "session", "payload"}
        if not required.issubset(envelope) or envelope.get("kind") != "request":
            raise CoreError("ENVELOPE_INVALID", "A complete request envelope is required.")
        if set(envelope) - REQUEST_KEYS:
            raise CoreError("ENVELOPE_INVALID", "Request envelope contains unknown fields.")
        if not isinstance(envelope["message_id"], str) or not envelope["message_id"]:
            raise CoreError("ENVELOPE_INVALID", "Message ID must be a nonempty string.")
        if envelope["protocol_version"] not in self.policy.protocol_versions:
            raise CoreError("INCOMPATIBLE_PROTOCOL", "The request protocol version is unsupported.")
        operation = envelope["operation"]
        if operation not in self.operations:
            raise CoreError("OPERATION_UNKNOWN", "The operation is not registered.")
        if not all(isinstance(envelope.get(name), dict) for name in ("harness", "session", "payload")):
            raise CoreError("ENVELOPE_INVALID", "Harness, session, and payload must be objects.")
        harness = envelope["harness"]
        if set(harness) != {"name", "version", "adapter_version"} or not all(
            isinstance(value, str) and value for value in harness.values()
        ):
            raise CoreError("ENVELOPE_INVALID", "Harness identity is invalid.")
        session = envelope["session"]
        if (
            set(session) != {"id", "state"}
            or not isinstance(session["id"], str)
            or not session["id"]
            or session["state"] not in SESSION_STATES
        ):
            raise CoreError("ENVELOPE_INVALID", "Session identity is invalid.")
        authentication = envelope.get("authentication")
        if authentication is not None and (
            not isinstance(authentication, dict)
            or set(authentication) != {"kind", "reference"}
            or authentication.get("kind") not in {"environment", "secret_store", "managed_identity"}
            or not isinstance(authentication.get("reference"), str)
            or not authentication["reference"]
        ):
            raise CoreError("AUTHENTICATION_REFERENCE_INVALID", "Authentication may contain references only.")
        if "extensions" in envelope and not isinstance(envelope["extensions"], dict):
            raise CoreError("ENVELOPE_INVALID", "Extensions must be an object.")
        payload = envelope["payload"]
        missing = set(self.operations[operation]["request_required"]) - set(payload)
        if missing:
            raise CoreError("PAYLOAD_INCOMPLETE", "Required operation fields are missing.", details={"missing": sorted(missing)})
        if FORBIDDEN_POLICY_KEYS.intersection(payload):
            raise CoreError("POLICY_INJECTION_DENIED", "Adapters cannot replace shared-core policy.")
        credential_paths = self._forbidden_paths(payload)
        if credential_paths:
            raise CoreError("CREDENTIAL_VALUE_DENIED", "Credential values must remain behind references.", details={"paths": credential_paths})
        return payload

    @classmethod
    def _forbidden_paths(cls, value: Any, path: str = "payload") -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key.casefold() in FORBIDDEN_CREDENTIAL_KEYS:
                    found.append(child_path)
                found.extend(cls._forbidden_paths(child, child_path))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                found.extend(cls._forbidden_paths(child, f"{path}[{index}]"))
        return found

    def _base(self, request: dict[str, Any], kind: str, session_state: str) -> dict[str, Any]:
        request_session = request.get("session")
        session_id = request_session.get("id", "unknown") if isinstance(request_session, dict) else "unknown"
        request_harness = request.get("harness")
        harness = request_harness if isinstance(request_harness, dict) else {"name": "unknown", "version": "unknown", "adapter_version": "unknown"}
        value = {
            "protocol_version": request.get("protocol_version", 2),
            "message_id": f"response-{request.get('message_id', 'unknown')}",
            "correlation_id": request.get("message_id", "unknown"),
            "kind": kind,
            "operation": request.get("operation", "unknown"),
            "harness": harness,
            "session": {"id": session_id, "state": session_state},
            "payload": {},
            "provider_accounting": self.accounting.snapshot(),
        }
        return value

    def _response(self, request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        session_id = request["session"]["id"]
        state = self.state.sessions.get(session_id, {}).get("state", request["session"].get("state", "active"))
        response = self._base(request, "response", state)
        response["payload"] = payload
        attribution = payload.pop("_context_attribution", None)
        if attribution is not None:
            response["context_attribution"] = attribution
        return response

    def _error(self, request: dict[str, Any], operation: str, error: CoreError) -> dict[str, Any]:
        response = self._base(request, "error", "failed")
        response["operation"] = operation
        response["error"] = error.public(operation)
        return response

    def _lifecycle_initialize(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected = negotiate_protocol(payload["supported_versions"], self.policy.protocol_versions)
        return {"selected_version": selected, "capabilities": sorted(self.handlers), "readiness": "ready"}

    def _lifecycle_shutdown(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"state": "closed"}

    def _capabilities_negotiate(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected = negotiate_protocol(payload["supported_versions"], self.policy.protocol_versions)
        offered = payload["capabilities"]
        if not isinstance(offered, list) or not all(isinstance(item, str) for item in offered):
            raise CoreError("CAPABILITIES_INVALID", "Capabilities must be string identifiers.")
        negotiated = sorted(set(offered).intersection(self.handlers))
        return {"selected_version": selected, "negotiated": negotiated, "degraded": len(negotiated) != len(set(offered))}

    def _session_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload["client_session_id"])
        if not session_id or session_id in self.state.sessions:
            raise CoreError("SESSION_INVALID", "Session ID is empty or already active.")
        self.state.sessions[session_id] = {"state": "active", "sequence": 0, "messages": []}
        return {"session_id": session_id, "state": "active"}

    def _session(self, session_id: str) -> dict[str, Any]:
        session = self.state.sessions.get(session_id)
        if session is None:
            raise CoreError("SESSION_NOT_FOUND", "Session is not registered.")
        return session

    def _session_continue(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session(payload["session_id"])
        if session["state"] == "closed":
            raise CoreError("SESSION_CLOSED", "Closed sessions cannot continue.")
        session["state"] = "active"
        return {"session_id": payload["session_id"], "state": "active"}

    def _session_detach(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session(payload["session_id"])
        if payload["last_sequence"] != session["sequence"]:
            raise CoreError("SESSION_SEQUENCE_STALE", "Detach sequence does not match.")
        session["state"] = "detached"
        reference = self.state.receipt({"session": payload["session_id"], "sequence": session["sequence"]})
        session["reattach_reference"] = reference
        return {"state": "detached", "reattach_reference": reference}

    def _session_reattach(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session(payload["session_id"])
        if session.get("reattach_reference") != payload["reattach_reference"]:
            raise CoreError("REATTACH_REFERENCE_INVALID", "Reattach reference does not match.")
        if payload["last_sequence"] > session["sequence"]:
            raise CoreError("SESSION_SEQUENCE_INVALID", "Reattach sequence is ahead of the core.")
        session["state"] = "active"
        return {"state": "active", "next_sequence": session["sequence"] + 1}

    def _message_submit(self, payload: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        if payload["role"] not in {"user", "assistant", "system", "tool"} or not isinstance(payload["content"], str):
            raise CoreError("MESSAGE_INVALID", "Message role or content is invalid.")
        if session_id is None:
            raise CoreError("SESSION_NOT_FOUND", "Message submission requires an envelope session.")
        session = self._session(session_id)
        if session["state"] != "active":
            raise CoreError("SESSION_NOT_ACTIVE", "Messages require an active session.")
        session["sequence"] += 1
        message_id = self.state.receipt({"sequence": session["sequence"], "role": payload["role"], "content": payload["content"]})
        session["messages"].append({"id": message_id, "sequence": session["sequence"], **payload})
        return {"message_id": message_id, "accepted": True}

    def _message_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session(payload["session_id"])
        after = payload["after_sequence"]
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise CoreError("SESSION_SEQUENCE_INVALID", "Stream sequence is invalid.")
        rows = [row for row in session["messages"] if row["sequence"] > after]
        if not rows:
            return {"sequence": session["sequence"], "delta": "", "terminal": True}
        row = rows[0]
        return {"sequence": row["sequence"], "delta": row["content"], "terminal": row["sequence"] == session["sequence"]}

    def _discover(self, kind: str, collection: str, payload: dict[str, Any]) -> dict[str, Any]:
        found = self.capabilities.discover(kind, payload["query"])
        return {collection: found["items"], "deferred_count": found["deferred_count"]}

    def _mcp_connect(self, payload: dict[str, Any]) -> dict[str, Any]:
        decision = decide_authority(action="write_external", resource=payload["server_id"], risk="medium", approval_reference=payload["approval_reference"], policy=self.policy)
        if decision["decision"] != "allow":
            raise CoreError("APPROVAL_REQUIRED", "MCP connection requires approval.")
        loaded = self.capabilities.load(payload["server_id"], "mcp")
        self.state.mcp_connections.add(payload["server_id"])
        return {"state": "connected", "capabilities": loaded["details"].get("capabilities", [])}

    def _tool_invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        decision = decide_authority(action="write_external", resource=payload["tool_id"], risk="medium", approval_reference=payload["approval_reference"], policy=self.policy)
        if decision["decision"] != "allow":
            raise CoreError("APPROVAL_REQUIRED", "Tool invocation requires approval.")
        self.capabilities.load(payload["tool_id"], "tool")
        handler = self.tool_handlers.get(payload["tool_id"])
        if handler is None:
            raise CoreError("CAPABILITY_UNBOUND", "The tool is discoverable but has no local binding.")
        if not isinstance(payload["arguments"], dict):
            raise CoreError("TOOL_ARGUMENTS_INVALID", "Tool arguments must be an object.")
        return {"result": handler(dict(payload["arguments"])), "state": "complete"}

    def _skill_load(self, payload: dict[str, Any]) -> dict[str, Any]:
        loaded = self.capabilities.load(payload["skill_id"], "skill")
        content = loaded["details"].get("content", "")
        return {"skill_id": payload["skill_id"], "instructions_hash": loaded["details_sha256"], "content": content}

    def _memory_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = str(payload["scope"])
        query = str(payload["query"]).casefold()
        records = [row for row in self.state.memory[scope] if query in json.dumps(row, sort_keys=True).casefold()]
        return {"records": records, "boundary": scope}

    def _memory_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload["records"], list):
            raise CoreError("MEMORY_RECORDS_INVALID", "Memory records must be a list.")
        scope = str(payload["scope"])
        self.state.memory[scope].extend(payload["records"])
        return {"accepted": len(payload["records"]), "receipt": self.state.receipt(payload)}

    def _retrieval_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload["query"]).casefold()
        sources = [row for row in self.retrieval_sources if query in (row.get("title", "") + " " + row.get("content", "")).casefold()]
        attributed = [
            {key: row[key] for key in ("source_reference", "uri", "title", "content_hash")}
            for row in sources
        ]
        return {"sources": attributed, "answer_context": [row.get("content", "") for row in sources]}

    def _routing_select(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .review.interface import classify_review
        result = select_prime_lane(payload["task"], payload["task_tier"], self.policy)
        result["review"] = classify_review(
            payload.get("review"), declared_paths=payload["task"].get("declared_paths", [])
        )
        return result

    def _omp_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        limits = payload["capability_limits"]
        if not isinstance(limits, dict) or limits.get("apply_changes") is not False:
            raise CoreError("OMP_LIMITS_INVALID", "OMP must return proposals without applying changes.")
        reference = f"worker-{payload['job_reference']}"
        self.omp_jobs[reference] = {"request": dict(payload), "patch": None}
        return {"accepted": True, "worker_reference": reference}

    def _omp_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        job = self.omp_jobs.get(payload["worker_reference"])
        if job is None:
            raise CoreError("OMP_JOB_NOT_FOUND", "OMP job is not registered.")
        digest = hashlib.sha256(json.dumps(job["request"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"patch_reference": f"proposal-{digest[:16]}", "source_hashes": {"request": digest}, "verification": {"status": "PASS", "applied": False}}

    def _authority_decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        return decide_authority(action=payload["action"], resource=payload["resource"], risk=payload["risk"], approval_reference=payload.get("approval_reference"), policy=self.policy)

    def _context_attribute(self, payload: dict[str, Any]) -> dict[str, Any]:
        selection = self.context_selections.get(payload["selection_reference"])
        if selection is None:
            raise CoreError("CONTEXT_SELECTION_NOT_FOUND", "Context selection is not registered.")
        return {"attribution": payload["selection_reference"], "_context_attribution": dict(selection["attribution"])}

    def _optimization_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        constraints = payload["constraints"]
        required = {"task_tier", "items"}
        optional = {"request_text", "available_tools"}
        if (
            not isinstance(constraints, dict)
            or not required.issubset(constraints)
            or not set(constraints).issubset(required | optional)
            or ({"request_text", "available_tools"}.intersection(constraints) and not optional.issubset(constraints))
        ):
            raise CoreError("OPTIMIZATION_CONSTRAINTS_INVALID", "Optimization constraints are closed.")
        selection = select_context(constraints["items"], constraints["task_tier"], self.policy)
        reference = str(payload["request_reference"])
        self.context_selections[reference] = selection
        result = {"selection": selection["selection"], "attribution": reference, "_context_attribution": dict(selection["attribution"])}
        if optional.issubset(constraints):
            request_text = constraints["request_text"]
            available_tools = constraints["available_tools"]
            if not isinstance(request_text, str) or not isinstance(available_tools, list) or any(not isinstance(item, str) for item in available_tools):
                raise CoreError("OPTIMIZATION_CONSTRAINTS_INVALID", "Tool projection inputs are invalid.")
            result["tool_projection"] = project_tools(request_text, available_tools, self.policy)
        return result

    def _provider_resolve(self, payload: dict[str, Any]) -> dict[str, Any]:
        constraints = payload["constraints"]
        if not isinstance(constraints, dict):
            raise CoreError("PROVIDER_CONSTRAINT_INVALID", "Provider constraints must be an object.")
        return self.providers.resolve(
            profile=payload["profile"],
            policy=self.policy,
            allowed_route_classes=constraints.get("allowed_route_classes"),
            approval_reference=constraints.get("approval_reference"),
            requirements=constraints.get("requirements"),
        )

    def _provider_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"accounting": str(payload["operation_reference"])}

    def _telemetry_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        metrics = payload["metrics"]
        if not isinstance(metrics, dict) or set(metrics) - TELEMETRY_METRIC_KEYS:
            raise CoreError("TELEMETRY_INVALID", "Telemetry metrics are not a closed content-free record.")
        latency = metrics.get("latency_ms")
        if latency is not None and (type(latency) is not int or latency < 0):
            raise CoreError("TELEMETRY_INVALID", "Telemetry latency must be a non-negative integer.")
        stages = metrics.get("stage_timings_ms", {})
        if (
            not isinstance(stages, dict)
            or set(stages) - TELEMETRY_STAGES
            or any(type(value) is not int or value < 0 for value in stages.values())
        ):
            raise CoreError("TELEMETRY_INVALID", "Telemetry stage timings are invalid.")
        normalized = dict(metrics)
        if stages:
            normalized["stage_timings_ms"] = dict(sorted(stages.items()))
        row = {"operation_reference": payload["operation_reference"], "metrics": normalized}
        self.state.telemetry.append(row)
        return {"accepted": True, "receipt": self.state.receipt(row)}

    def _health_readiness(self, payload: dict[str, Any]) -> dict[str, Any]:
        components = payload["components"]
        if not isinstance(components, list) or not all(isinstance(item, str) and item for item in components):
            raise CoreError("HEALTH_COMPONENTS_INVALID", "Health components must be identifiers.")
        readiness = self.capabilities.readiness()
        built_in = {"policy", "protocol", "accounting"}
        values = {name: (True if name in built_in else readiness.get(name, False)) for name in components}
        return {"overall": "ready" if all(values.values()) else "degraded", "components": values}

    def _activation_transition(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.state.transition_activation(payload, self.policy)

    def _rollback_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.state.rollback(payload)

    def _verification_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        hashes = payload["source_hashes"]
        if not isinstance(hashes, dict) or not hashes or any(
            not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
            for name, digest in hashes.items()
        ):
            raise CoreError("SOURCE_HASHES_INVALID", "Verification requires SHA-256 source hashes.")
        gates = set(payload["gates"]) if isinstance(payload["gates"], list) else set()
        passed = self.policy.required_promotion_gates.issubset(gates)
        return {"decision": "PASS" if passed else "HOLD", "evidence": {"subject_reference": payload["subject_reference"], "gates": sorted(gates)}, "source_hashes": dict(sorted(hashes.items()))}
