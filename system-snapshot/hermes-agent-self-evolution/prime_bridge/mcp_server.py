"""Managed Hermes MCP surface for explicit, bounded Prime delegation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from prime_bridge.capability_broker import CapabilityBroker
from prime_bridge.capability_ledger import CapabilityLedger
from prime_bridge.capability_policy import CapabilityPolicy
from prime_bridge.contained_worker import ContainedPrimeWorker
from prime_bridge.connectivity_ledger import ConnectivityLedger
from prime_bridge.connectivity_policy import ConnectivityPolicy
from prime_bridge.connectivity_service import ConnectivityService
from prime_bridge.admission import AdmissionController, system_resource_snapshot
from prime_bridge.auto_handoff import auto_handoff
from prime_bridge.hermes_mcp_client import HermesMCPClient
from prime_bridge.intent_router import IntentPolicy
from prime_bridge.job_cli import load_runtime_authority
from prime_bridge.job_ledger import JobLedger
from prime_bridge.learning_ledger import LearningLedger
from prime_bridge.learning_service import LearningService
from prime_bridge.profile_policy import (
    PrimeProfilePolicy,
    environment_context,
    restriction_projection,
)
from prime_bridge.promotion_engine import PromotionEngine
from prime_bridge.promotion_policy import PromotionPolicy
from prime_bridge.operations import RolloutDashboard
from prime_bridge.rollout_policy import RolloutPolicy
from prime_bridge.session_control import PrimeSessionControl, SessionControlError
from prime_bridge.supervisor import PrimePolicy, PrimeSupervisor

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_AUTHORITY = ROOT / "config/prime-runtime-authority.json"
PHASE1_CONTEXT = ROOT / ".planning/phases/01-contracts-feasibility/evidence/phase01-run-context.json"
CAPABILITY_POLICY = ROOT / "config/prime-capability-policy.json"
LEARNING_POLICY = ROOT / "config/prime-learning-policy.json"
CONNECTIVITY_POLICY = ROOT / "config/connectivity-authority.json"
ROLLOUT_POLICY = ROOT / "config/prime-rollout-policy.json"
AUTO_ROUTE_POLICY = ROOT / "config/prime-auto-route-policy.json"


class PrimeToolError(RuntimeError):
    pass


PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _profile_config_for_request(current_config: Path, profile: str) -> Path:
    """Resolve a selected sibling profile without permitting path traversal."""
    if not PROFILE_NAME.fullmatch(profile):
        raise PrimeToolError("selected Prime profile is not canonical")
    profiles_root = current_config.parent.parent
    candidate = profiles_root / profile / "config.yaml"
    if not candidate.is_file():
        raise PrimeToolError("selected Prime profile config is unavailable")
    return candidate


def _profile_route(profile: str, config_path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    config_bytes = config_path.read_bytes()
    config = yaml.safe_load(config_bytes) or {}
    context = json.loads(PHASE1_CONTEXT.read_text(encoding="utf-8"))
    verified = context["prerequisites"]["model_route"]
    model_id = str(verified["model"])
    provider = str(verified["source_provider"]).removeprefix("custom:")
    providers = config.get("providers") or {}
    if provider not in providers:
        raise PrimeToolError("shared Prime controller provider is unavailable in the selected profile")
    endpoint = str(verified["endpoint"])
    route = {
        "provider": str(verified["provider"]), "model": model_id,
        "api": "openai-completions", "endpoint": endpoint,
    }
    public = {"provider": route["provider"], "model": model_id, "api": route["api"], "endpoint": endpoint}
    route["fingerprint"] = JobLedger.route_fingerprint(public)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    prime_config = {
        "provider_id": route["provider"], "baseUrl": endpoint, "api": route["api"],
        "apiKey": {"source": "hermes-provider-config", "providerId": provider},
        "models": [{
            "id": model_id, "contextWindow": 131072, "maxTokens": 8192,
            "supportsStreaming": True, "supportsTools": True,
        }],
        "compat": {"supportsDeveloperRole": True, "supportsReasoningEffort": False},
        "authority": {"sourcePath": str(config_path), "sourceSha256": hashlib.sha256(config_bytes).hexdigest()},
        "activeProfile": profile, "runId": uuid.uuid4().hex, "capturedAt": now,
    }
    return route, prime_config


class PrimeToolService:
    def __init__(
        self, *, profile: str, mode: str, policy_path: Path, ledger_path: Path,
        profile_config: Path, allowed_roots: list[Path] | None = None,
        worker_factory: Any | None = None,
        capability_policy_path: Path = CAPABILITY_POLICY,
        capability_ledger_path: Path | None = None,
        learning_policy_path: Path = LEARNING_POLICY,
        learning_ledger_path: Path | None = None,
        learning_shared_root: Path | None = None,
        connectivity_policy_path: Path = CONNECTIVITY_POLICY,
        connectivity_ledger_path: Path | None = None,
        rollout_policy_path: Path = ROLLOUT_POLICY,
        resource_provider: Any = system_resource_snapshot,
    ):
        self.profile = profile
        self.mode = mode
        self.policy_path = policy_path
        self.profile_config = profile_config
        self.policy = PrimeProfilePolicy.load(policy_path)
        if self.policy.mode_for(profile) != mode:
            raise PrimeToolError("profile mode does not match central policy")
        self.ledger = JobLedger(ledger_path, allowed_roots or [Path("__WSL_WORKSPACE__")])
        self.capability_policy = CapabilityPolicy.load(capability_policy_path)
        self.capability_ledger = CapabilityLedger(
            capability_ledger_path or ledger_path.with_name("capabilities.sqlite3"),
            approval_ttl_seconds=self.capability_policy.approval_ttl_seconds,
        )
        self.capability_adapter = HermesMCPClient(profile_config)
        self.learning_policy = PromotionPolicy.load(learning_policy_path)
        self.learning_ledger = LearningLedger(
            learning_ledger_path or ledger_path.with_name("learning.sqlite3"),
            max_content_bytes=self.learning_policy.max_content_bytes,
        )
        self.learning_service = LearningService(self.learning_ledger, self.ledger)
        self.promotion_engine = PromotionEngine(
            self.learning_ledger, self.learning_policy, shared_root=learning_shared_root,
        )
        self.connectivity_policy = ConnectivityPolicy.load(connectivity_policy_path)
        self.connectivity_ledger = ConnectivityLedger(
            connectivity_ledger_path or ledger_path.with_name("connectivity.sqlite3"),
        )
        self.connectivity_service = ConnectivityService(self.connectivity_policy, self.connectivity_ledger)
        self.rollout_policy = RolloutPolicy.load(rollout_policy_path)
        self.admission_controller = AdmissionController(self.rollout_policy.admission, resource_provider)
        self.rollout_dashboard = RolloutDashboard()
        self.worker_factory = worker_factory
        self.session_control = PrimeSessionControl(
            self.ledger, profile=self.profile, mode=self.mode, enabled=self.policy.enabled,
        )
        self._session_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}

    def for_profile(self, profile: str) -> "PrimeToolService":
        """Create a request-scoped service that preserves the selected profile's authority."""
        if profile == self.profile:
            return self
        config = _profile_config_for_request(self.profile_config, profile)
        return PrimeToolService(
            profile=profile,
            mode=self.policy.mode_for(profile),
            policy_path=self.policy_path,
            ledger_path=self.ledger.path,
            profile_config=config,
            allowed_roots=list(self.ledger.allowed_roots),
            worker_factory=self.worker_factory,
        )

    def _prepare_job(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], PrimeSupervisor]:
        if not self.policy.permits(self.profile):
            raise PrimeToolError("Prime delegation is disabled for this profile")
        if arguments.get("explicit_handoff") is not True:
            raise PrimeToolError("explicit_handoff=true is required; existing sessions remain native")
        prompt = arguments.get("prompt")
        workspace = arguments.get("workspace")
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(workspace, str):
            raise PrimeToolError("prompt and workspace are required")
        route, prime_config = _profile_route(self.profile, self.profile_config)
        runtime = load_runtime_authority(RUNTIME_AUTHORITY)
        config = yaml.safe_load(self.profile_config.read_text(encoding="utf-8")) or {}
        restrictions = restriction_projection(config)
        record = self.ledger.create(
            owner="hermes-mcp", profile=self.profile,
            project=str(arguments.get("project") or Path(workspace).name),
            session_id=str(arguments.get("session_id") or f"handoff-{uuid.uuid4().hex}"),
            workspace=workspace, route=route, runtime=runtime,
            limits={
                "max_iterations": int(arguments.get("max_iterations", 20)),
                "no_progress_seconds": int(arguments.get("no_progress_seconds", 300)),
                "wall_seconds": int(arguments.get("wall_seconds", 3600)),
            },
        )
        self.ledger.note(record["job_id"], "profile_policy", {
            "mode": self.mode, "restriction_fingerprint": restrictions["fingerprint"],
            "explicit_handoff": True, "native_fallback": True,
        })
        def default_factory(job: dict[str, Any]) -> ContainedPrimeWorker:
            broker = CapabilityBroker(
                self.capability_policy, self.capability_ledger,
                self.capability_adapter, job_ledger=self.ledger,
                internal_handlers={"prime-learning": self.learning_service.call},
            )
            return ContainedPrimeWorker(
                repo_root=ROOT, runtime_authority=RUNTIME_AUTHORITY,
                prime_config=prime_config, hermes_config=self.profile_config,
                capability_broker=broker, job_id=job["job_id"], profile=self.profile,
            )
        factory = self.worker_factory or default_factory
        supervisor = PrimeSupervisor(
            self.ledger, factory,
            PrimePolicy(
                profile_modes={self.profile: self.mode},
                allowed_route_fingerprints=frozenset({route["fingerprint"]}),
                allowed_runtime_fingerprints=frozenset({runtime["fingerprint"]}),
            ),
            admission_controller=self.admission_controller,
        )
        return record, supervisor

    async def delegate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        record, supervisor = self._prepare_job(arguments)
        return await supervisor.run(record["job_id"], arguments["prompt"])

    async def start_session(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = arguments.get("project")
        session_id = arguments.get("session_id")
        if not isinstance(project, str) or not isinstance(session_id, str):
            raise PrimeToolError("project and session_id are required for a Prime-backed session")
        try:
            self.session_control.require_no_active(project=project, session_id=session_id)
        except SessionControlError as exc:
            raise PrimeToolError(str(exc)) from exc
        record, supervisor = self._prepare_job(arguments)
        self.ledger.note(record["job_id"], "session_handoff", {
            "hermes_session_id": session_id,
            "engine": "prime",
            "mode": self.mode,
            "existing_session_converted": False,
        })
        try:
            task = supervisor.submit(
                record["job_id"], arguments["prompt"], owner="hermes-session-supervisor",
            )
        except Exception as exc:
            self.ledger.transition(
                record["job_id"], "cancelled",
                last_error=f"session admission rejected: {type(exc).__name__}",
            )
            raise PrimeToolError("Prime session admission failed; native fallback remains active") from exc
        self._session_tasks[record["job_id"]] = task

        def complete(done: asyncio.Task[dict[str, Any]]) -> None:
            self._session_tasks.pop(record["job_id"], None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(complete)
        # Return the durable job receipt before yielding to the submitted worker.
        # Worker startup may perform container/process setup; yielding here can make
        # the MCP request exceed the gateway acknowledgement timeout even though the
        # job was successfully admitted.
        return self.session_control.status(record["job_id"], session_id=session_id)

    async def start_auto_routed_session(self, arguments: dict[str, Any], *, readiness: bool,
                                        compatibility: bool, admitted: bool) -> dict[str, Any]:
        """Gateway adapter entry point; failed gates intentionally return native route data."""
        return await auto_handoff(
            self, message=str(arguments.get("prompt", "")), profile=self.profile, mode=self.mode,
            session_id=str(arguments.get("session_id", "")), workspace=str(arguments.get("workspace", "")),
            project=str(arguments.get("project", "")), readiness=readiness,
            compatibility=compatibility, admitted=admitted,
            policy=IntentPolicy.load(AUTO_ROUTE_POLICY),
        )

    def approve_session(self, arguments: dict[str, Any]) -> dict[str, Any]:
        job = self.session_control.owned(
            arguments["job_id"], session_id=arguments["session_id"],
        )
        request = self.capability_ledger.get(arguments["request_id"])
        if request["job_id"] != job["job_id"] or request["profile"] != self.profile:
            raise PrimeToolError("capability approval is not bound to this Prime session")
        return self.capability_ledger.resolve(
            arguments["request_id"], profile=self.profile,
            decision=arguments["decision"], approver=f"hermes-session-{self.profile}",
            operator_nonce=arguments["operator_nonce"],
        )

    def health(self) -> dict[str, Any]:
        capability = CapabilityBroker(
            self.capability_policy, self.capability_ledger, self.capability_adapter,
            internal_handlers={"prime-learning": self.learning_service.call},
        ).health()
        return {
            "status": "ready" if self.policy.permits(self.profile) else "native-only",
            "profile": self.profile, "mode": self.mode, "native_fallback": True,
            "explicit_handoff_required": True, "activation": "restart-boundary",
            "capabilities": capability,
            "learning": {
                "local_ledger": "ready", "promotion_policy": "ready" if self.learning_policy.enabled else "disabled",
                "shared_authority": "hermes", "prime_direct_shared_write": False,
            },
            "connectivity": {
                "authority_machine": self.connectivity_policy.authority_machine,
                "desktop_role": "independent-verifier",
                "prime_direct_repair": False,
            },
        }

    def connectivity_status(self) -> dict[str, Any]:
        return self.connectivity_service.status()

    def connectivity_assess_change(self, change: str) -> dict[str, str]:
        return self.connectivity_service.assess_change(change)

    def rollout_status(self) -> dict[str, Any]:
        capability = CapabilityBroker(
            self.capability_policy, self.capability_ledger, self.capability_adapter,
            internal_handlers={"prime-learning": self.learning_service.call},
        ).health()
        try:
            phase1 = json.loads(PHASE1_CONTEXT.read_text(encoding="utf-8"))
            model_ready = phase1.get("prerequisites", {}).get("model_route", {}).get("status") == "READY"
        except (OSError, UnicodeError, json.JSONDecodeError):
            model_ready = False
        connectivity = self.connectivity_service.status()
        counts = connectivity.get("counts", {})
        client_ready = bool(connectivity.get("resources")) and counts.get("HEALTHY") == len(connectivity["resources"])
        capability_ready = (
            capability.get("liveness") == "ready"
            and capability.get("policy") == "ready"
            and capability.get("mcp_transport") == "configured"
            and capability.get("retrieval") == "configured"
        )
        axes = {
            "bridge_liveness": {"status": "PASS", "reason": "managed-mcp-process-running"},
            "runtime_compatibility": {"status": "PASS", "reason": "pinned-runtime-authority-loaded"},
            "capability_readiness": {
                "status": "PASS" if capability_ready else "FAIL",
                "reason": "capability-broker-ready" if capability_ready else "capability-broker-unready",
            },
            "model_auth_readiness": {
                "status": "PASS" if model_ready else "UNVERIFIED",
                "reason": "probed-model-route" if model_ready else "model-route-not-proved",
            },
            "remote_client_reachability": {
                "status": "PASS" if client_ready else "DEFERRED",
                "reason": "connectivity-authority-healthy" if client_ready else "live-client-not-currently-proved",
            },
            "profile_rollout": {
                "status": "PASS" if self.rollout_policy.profile_eligible(self.profile) else "FAIL",
                "reason": f"stage-{self.rollout_policy.stage}",
            },
        }
        value = self.rollout_dashboard.evaluate(axes, emergency_disabled=not self.policy.enabled)
        value["profile"] = self.profile
        value["stage"] = self.rollout_policy.stage
        value["admission"] = self.admission_controller.last_decision
        return value

    def learning_catalog(self) -> dict[str, Any]:
        records = self.learning_ledger.list_records(profile=self.profile)
        proposals = self.learning_ledger.list_proposals(profile=self.profile)
        return {
            "profile": self.profile,
            "records": [{key: item[key] for key in (
                "record_id", "project", "session_id", "scope", "subject",
                "content_hash", "privacy", "status", "created_at", "expires_at",
            )} for item in records],
            "proposals": [{key: item[key] for key in (
                "proposal_id", "record_id", "project", "lane", "target_scope",
                "subject", "dedupe_key", "status", "expires_at",
            )} for item in proposals],
        }

    def validate_promotion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        proposal = self.learning_ledger.get_proposal(arguments["proposal_id"])
        if proposal["profile"] != self.profile:
            raise PrimeToolError("promotion belongs to another profile")
        validated = self.learning_ledger.validate(
            proposal["proposal_id"], validator=f"hermes-{self.profile}",
            mode=arguments["mode"], outcome=arguments["outcome"],
            evidence_hash=arguments["evidence_hash"],
        )
        if validated["status"] == "validated":
            return self.promotion_engine.evaluate(validated["proposal_id"])
        return validated

    def resolve_promotion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        proposal = self.learning_ledger.get_proposal(arguments["proposal_id"])
        if proposal["profile"] != self.profile:
            raise PrimeToolError("promotion belongs to another profile")
        return self.promotion_engine.resolve(
            proposal["proposal_id"], decision=arguments["decision"],
            approver=f"hermes-{self.profile}",
        )

    def revoke_promotion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        proposal = self.learning_ledger.get_proposal(arguments["proposal_id"])
        if proposal["profile"] != self.profile:
            raise PrimeToolError("promotion belongs to another profile")
        return self.promotion_engine.revoke(
            proposal["proposal_id"], approver=f"hermes-{self.profile}",
            reason=arguments["reason"],
        )

    def pending_approvals(self) -> dict[str, Any]:
        requests = self.capability_ledger.list(profile=self.profile, pending=True)
        public = [{
            key: item[key] for key in (
                "request_id", "alias", "classification", "argument_hash",
                "argument_keys", "created_at", "expires_at", "operator_nonce",
            )
        } for item in requests]
        return {"profile": self.profile, "requests": public}

    def resolve_approval(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.capability_ledger.resolve(
            arguments["request_id"], profile=self.profile,
            decision=arguments["decision"], approver=f"hermes-{self.profile}",
            operator_nonce=arguments["operator_nonce"],
        )

    def owned_job(self, job_id: str) -> dict[str, Any]:
        job = self.ledger.get(job_id)
        if job["profile"] != self.profile:
            raise PrimeToolError("Prime job is not owned by the current profile")
        return job


def _tools() -> list[Any]:
    from mcp import types
    return [
        types.Tool(name="prime_delegate", description="Explicitly hand one bounded task to Prime Agent while this Hermes session remains native.", inputSchema={"type":"object","properties":{"prompt":{"type":"string"},"workspace":{"type":"string"},"project":{"type":"string"},"session_id":{"type":"string"},"explicit_handoff":{"const":True},"max_iterations":{"type":"integer","minimum":1,"maximum":100},"no_progress_seconds":{"type":"integer","minimum":5,"maximum":3600},"wall_seconds":{"type":"integer","minimum":10,"maximum":14400}},"required":["prompt","workspace","explicit_handoff"],"additionalProperties":False}),
        types.Tool(name="prime_job_status", description="Read one durable Prime job record.", inputSchema={"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"],"additionalProperties":False}),
        types.Tool(name="prime_job_list", description="List Prime jobs owned by the current Hermes profile.", inputSchema={"type":"object","properties":{},"additionalProperties":False}),
        types.Tool(name="prime_job_steer", description="Queue a bounded steering message for a running Prime job.", inputSchema={"type":"object","properties":{"job_id":{"type":"string"},"text":{"type":"string"}},"required":["job_id","text"],"additionalProperties":False}),
        types.Tool(name="prime_job_cancel", description="Queue cancellation for a nonterminal Prime job.", inputSchema={"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"],"additionalProperties":False}),
        types.Tool(name="prime_bridge_health", description="Show Prime availability, profile mode, and native fallback state.", inputSchema={"type":"object","properties":{},"additionalProperties":False}),
        types.Tool(name="prime_capability_catalog", description="List public Prime capability aliases without MCP targets or credentials.", inputSchema={"type":"object","properties":{},"additionalProperties":False}),
        types.Tool(name="prime_capability_approvals", description="List approval requests owned by the current Hermes profile.", inputSchema={"type":"object","properties":{},"additionalProperties":False}),
        types.Tool(name="prime_capability_resolve", description="Approve or deny one profile-owned high-impact Prime capability request after native user review.", inputSchema={"type":"object","properties":{"request_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"operator_nonce":{"type":"string","pattern":"^[0-9a-f]{32}$"},"decision":{"enum":["approved","denied"]}},"required":["request_id","operator_nonce","decision"],"additionalProperties":False}, annotations=types.ToolAnnotations(destructiveHint=True, idempotentHint=False, openWorldHint=True)),
        types.Tool(name="prime_learning_list", description="List namespaced Prime-local learning and promotion metadata for this profile.", inputSchema={"type":"object","properties":{},"additionalProperties":False}, annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
        types.Tool(name="prime_promotion_validate", description="Record a host-owned validation result and automatically promote only when the closed lane policy permits it.", inputSchema={"type":"object","properties":{"proposal_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"mode":{"enum":["deterministic","independent","manual"]},"outcome":{"enum":["passed","failed"]},"evidence_hash":{"type":"string","pattern":"^[0-9a-f]{64}$"}},"required":["proposal_id","mode","outcome","evidence_hash"],"additionalProperties":False}, annotations=types.ToolAnnotations(destructiveHint=True, idempotentHint=False, openWorldHint=False)),
        types.Tool(name="prime_promotion_resolve", description="Approve or deny an approval-gated learning promotion owned by this profile.", inputSchema={"type":"object","properties":{"proposal_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"decision":{"enum":["approved","denied"]}},"required":["proposal_id","decision"],"additionalProperties":False}, annotations=types.ToolAnnotations(destructiveHint=True, idempotentHint=False, openWorldHint=False)),
        types.Tool(name="prime_promotion_revoke", description="Revoke an active shared learning artifact while preserving rollback bytes and audit history.", inputSchema={"type":"object","properties":{"proposal_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"reason":{"type":"string","minLength":1,"maxLength":1024}},"required":["proposal_id","reason"],"additionalProperties":False}, annotations=types.ToolAnnotations(destructiveHint=True, idempotentHint=False, openWorldHint=False)),
        types.Tool(name="prime_promotion_expire", description="Expire stale unapplied promotion proposals owned by this profile.", inputSchema={"type":"object","properties":{},"additionalProperties":False}, annotations=types.ToolAnnotations(destructiveHint=True, idempotentHint=True, openWorldHint=False)),
        types.Tool(name="prime_connectivity_status", description="Read desired-versus-observed NUCBOX, __OWNER_HANDLE__ desktop, mount, tunnel, and VPS topology without exposing credentials.", inputSchema={"type":"object","properties":{},"additionalProperties":False}, annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
        types.Tool(name="prime_connectivity_assess_change", description="Classify a named connectivity change as an exact repair envelope, approval-required, or rejected. This tool does not execute repairs.", inputSchema={"type":"object","properties":{"change":{"enum":["gateway_host_binding","tailscale_acl","firewall_exposure","credential","exit_node","subnet_route","new_mount","broad_permission","dashboard_exposure","service_ownership"]}},"required":["change"],"additionalProperties":False}, annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
        types.Tool(name="prime_session_route", description="Describe the current profile's new-session engine recommendation and native fallback without changing a session.", inputSchema={"type":"object","properties":{},"additionalProperties":False}, annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
        types.Tool(name="prime_session_start", description="Explicitly hand a new or selected Hermes session to the bounded Prime runtime; existing native sessions are never silently converted.", inputSchema={"type":"object","properties":{"prompt":{"type":"string","minLength":1},"workspace":{"type":"string","minLength":1},"project":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},"explicit_handoff":{"const":True},"max_iterations":{"type":"integer","minimum":1},"no_progress_seconds":{"type":"integer","minimum":1},"wall_seconds":{"type":"integer","minimum":1}},"required":["prompt","workspace","project","session_id","explicit_handoff"],"additionalProperties":False}),
        types.Tool(name="prime_auto_session_start", description="Gateway-only automatic Prime handoff for an already-classified operational request. This uses the canonical Ponytail policy and falls back with a route receipt.", inputSchema={"type":"object","properties":{"profile":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},"prompt":{"type":"string","minLength":1},"workspace":{"type":"string","minLength":1},"project":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},"readiness":{"type":"boolean"},"compatibility":{"type":"boolean"},"admitted":{"type":"boolean"}},"required":["profile","prompt","workspace","project","session_id","readiness","compatibility","admitted"],"additionalProperties":False}),
        types.Tool(name="prime_session_status", description="Read a sanitized profile-owned Prime-backed session snapshot.", inputSchema={"type":"object","properties":{"job_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"}},"required":["job_id","session_id"],"additionalProperties":False}, annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
        types.Tool(name="prime_session_events", description="Read bounded incremental Prime-backed session status events after a monotonic cursor.", inputSchema={"type":"object","properties":{"job_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},"after_event_id":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":200}},"required":["job_id","session_id"],"additionalProperties":False}, annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
        types.Tool(name="prime_session_steer", description="Queue bounded steering for the exact running Prime-backed session.", inputSchema={"type":"object","properties":{"job_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},"text":{"type":"string","minLength":1,"maxLength":12000}},"required":["job_id","session_id","text"],"additionalProperties":False}),
        types.Tool(name="prime_session_approve", description="Approve or deny a high-impact capability request bound to the exact Prime-backed session.", inputSchema={"type":"object","properties":{"job_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},"request_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"operator_nonce":{"type":"string","pattern":"^[0-9a-f]{32}$"},"decision":{"enum":["approved","denied"]}},"required":["job_id","session_id","request_id","operator_nonce","decision"],"additionalProperties":False}, annotations=types.ToolAnnotations(destructiveHint=True, idempotentHint=False, openWorldHint=True)),
        types.Tool(name="prime_session_interrupt", description="Queue cancellation for the exact Prime-backed session without affecting native Hermes.", inputSchema={"type":"object","properties":{"job_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"}},"required":["job_id","session_id"],"additionalProperties":False}, annotations=types.ToolAnnotations(destructiveHint=True, idempotentHint=True, openWorldHint=False)),
        types.Tool(name="prime_session_detach", description="Detach the Hermes OS view while the Prime job continues under the backend supervisor.", inputSchema={"type":"object","properties":{"job_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"}},"required":["job_id","session_id"],"additionalProperties":False}),
        types.Tool(name="prime_session_reattach", description="Reattach the exact Hermes profile/session view to its durable Prime job.", inputSchema={"type":"object","properties":{"job_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"session_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"}},"required":["job_id","session_id"],"additionalProperties":False}),
        types.Tool(name="prime_rollout_status", description="Read multi-axis Prime rollout readiness, resource admission, and native fallback without changing services.", inputSchema={"type":"object","properties":{},"additionalProperties":False}, annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
    ]


async def serve() -> None:
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    profile, mode, policy, ledger, config = environment_context()
    service = PrimeToolService(
        profile=profile, mode=mode, policy_path=policy, ledger_path=ledger, profile_config=config,
    )
    app = Server("prime-agent")

    @app.list_tools()
    async def list_tools():
        return _tools()

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]):
        try:
            if name == "prime_delegate":
                result = await service.delegate(arguments)
            elif name == "prime_job_status":
                result = service.owned_job(arguments["job_id"])
            elif name == "prime_job_list":
                result = {"jobs": service.ledger.list(profile=service.profile)}
            elif name == "prime_job_steer":
                service.owned_job(arguments["job_id"])
                result = {
                    "command_id": service.ledger.enqueue_command(
                        arguments["job_id"], "steer", arguments["text"],
                    ),
                    "status": "queued",
                }
            elif name == "prime_job_cancel":
                job = service.owned_job(arguments["job_id"])
                result = service.ledger.transition(arguments["job_id"], "cancelled") if job["status"] == "created" else {"command_id": service.ledger.enqueue_command(arguments["job_id"], "cancel"), "status": "queued"}
            elif name == "prime_bridge_health":
                result = service.health()
            elif name == "prime_capability_catalog":
                result = {"aliases": service.capability_policy.public_catalog()}
            elif name == "prime_capability_approvals":
                result = service.pending_approvals()
            elif name == "prime_capability_resolve":
                result = service.resolve_approval(arguments)
            elif name == "prime_learning_list":
                result = service.learning_catalog()
            elif name == "prime_promotion_validate":
                result = service.validate_promotion(arguments)
            elif name == "prime_promotion_resolve":
                result = service.resolve_promotion(arguments)
            elif name == "prime_promotion_revoke":
                result = service.revoke_promotion(arguments)
            elif name == "prime_promotion_expire":
                result = {"expired": service.promotion_engine.expire(profile=service.profile)}
            elif name == "prime_connectivity_status":
                result = service.connectivity_status()
            elif name == "prime_connectivity_assess_change":
                result = service.connectivity_assess_change(arguments["change"])
            elif name == "prime_session_route":
                result = service.session_control.route()
            elif name == "prime_session_start":
                result = await service.start_session(arguments)
            elif name == "prime_auto_session_start":
                request_service = service.for_profile(arguments["profile"])
                result = await request_service.start_auto_routed_session(
                    arguments,
                    readiness=arguments["readiness"], compatibility=arguments["compatibility"],
                    admitted=arguments["admitted"],
                )
            elif name == "prime_session_status":
                result = service.session_control.status(arguments["job_id"], session_id=arguments["session_id"])
            elif name == "prime_session_events":
                result = service.session_control.events(
                    arguments["job_id"], session_id=arguments["session_id"],
                    after_event_id=arguments.get("after_event_id", 0), limit=arguments.get("limit", 100),
                )
            elif name == "prime_session_steer":
                result = service.session_control.steer(
                    arguments["job_id"], session_id=arguments["session_id"], text=arguments["text"],
                )
            elif name == "prime_session_approve":
                result = service.approve_session(arguments)
            elif name == "prime_session_interrupt":
                result = service.session_control.interrupt(arguments["job_id"], session_id=arguments["session_id"])
            elif name == "prime_session_detach":
                result = service.session_control.detach(arguments["job_id"], session_id=arguments["session_id"])
            elif name == "prime_session_reattach":
                result = service.session_control.reattach(arguments["job_id"], session_id=arguments["session_id"])
            elif name == "prime_rollout_status":
                result = service.rollout_status()
            else:
                raise PrimeToolError("unknown Prime MCP tool")
            return [types.TextContent(type="text", text=json.dumps(result, sort_keys=True))]
        except Exception as exc:  # noqa: BLE001 - collapse every MCP-boundary failure safely
            return [types.TextContent(type="text", text=json.dumps({
                "error": type(exc).__name__, "message": "Prime MCP request failed",
            }, sort_keys=True))]

    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def main() -> int:
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
