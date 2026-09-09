"""Optional Prime-to-governed-session bootstrap with no implicit storage."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ELIGIBLE_LANES = frozenset({"T3_PRIME_OMP", "T4_AUTONOMOUS"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


class GovernedSessionConflict(RuntimeError):
    """Canonical session identity or authority conflicts with the Prime job."""


class GovernedPrimeSession:
    """Open, job-scoped trajectory and lifecycle handle owned by Prime's supervisor."""

    def __init__(
        self, service: Any, capture: Any, *, task_id: str, run_id: str,
        session_id: str, actual_lane: str,
    ) -> None:
        self.service = service
        self.capture = capture
        self.task_id = task_id
        self.run_id = run_id
        self.session_id = session_id
        self.actual_lane = actual_lane
        self._closed = False

    def lifecycle(self, state: str, *, reason: str = "prime supervisor lifecycle") -> None:
        """Synchronize Prime lifecycle idempotently into the canonical run projection."""
        target = {
            "starting": "admitted", "running": "running", "completed": "completed",
            "failed": "failed", "cancelled": "cancelled", "orphaned": "failed",
        }.get(state)
        if target is None:
            return
        run = self.service.autonomous_run(self.run_id)
        current = str(run["lifecycle_state"])
        if current == target or current in {"completed", "failed", "cancelled"}:
            return
        if current == "requested" and target not in {"admitted", "cancelled"}:
            self.service.transition_autonomous_run(
                self.run_id, target="admitted", reason="Prime worker admitted",
            )
            current = "admitted"
        if current == "admitted" and target in {"completed", "failed"}:
            self.service.transition_autonomous_run(
                self.run_id, target="running", reason="Prime worker entered execution",
            )
        self.service.transition_autonomous_run(self.run_id, target=target, reason=reason)
        if target in {"completed", "failed", "cancelled"}:
            event_type = {
                "completed": "run.completed", "failed": "run.failed",
                "cancelled": "run.cancelled",
            }[target]
            self.capture.governance(
                event_type, f"prime-lifecycle:{self.run_id}:{target}",
                {"run_id": self.run_id, "prime_state": state, "reason_digest": _digest(reason)},
            )

    def snapshot_before_apply(
        self, *, turn_id: str, workspace: Path, relative_paths: tuple[str, ...],
    ) -> dict[str, Any]:
        """Record exact pre-edit bytes through the existing canonical store."""
        root = workspace.resolve()
        files: dict[str, bytes | None] = {}
        for relative in relative_paths:
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError("workspace snapshot path escapes project root")
            files[relative] = candidate.read_bytes() if candidate.is_file() else None
        return self.service.record_workspace_snapshot(
            self.session_id, turn_id=turn_id, files=files, actor="prime",
        )

    def record_post_edit_result(
        self, *, turn_id: str, host_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Project bounded host verification as next-turn diagnostics."""
        test = host_result.get("test") if isinstance(host_result, dict) else None
        test = test if isinstance(test, dict) else {}
        status = str(host_result.get("status", "VERIFIED_FAILED"))
        diagnostics = [] if status == "VERIFIED_OK" else [{
            "path": str((host_result.get("rolled_back") or host_result.get("applied") or ["workspace"])[0]),
            "severity": "error",
            "message": str(test.get("error") or host_result.get("reason") or "host verification failed")[:500],
            "code": str(host_result.get("reason") or test.get("command_id") or status)[:128],
        }]
        return self.service.record_workspace_diagnostics(
            self.session_id, turn_id=turn_id, diagnostics=diagnostics, actor="prime",
        )

    def close(self) -> dict[str, Any]:
        if self._closed:
            return {"status": "PASS", "closed": True}
        self._closed = True
        health = self.capture.close()
        self.service.close()
        return {**health, "closed": True}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scope_id(project: str) -> str:
    if _SAFE_ID.fullmatch(project):
        return project
    return f"project-{_digest(project)[:24]}"


@dataclass(frozen=True)
class GovernedSessionBridge:
    """Lazily opens the explicitly configured canonical executor stores."""

    executor_db: Path | None
    executor_source: Path | None
    unavailable_reason: str | None = None

    @classmethod
    def from_environment(cls) -> "GovernedSessionBridge":
        database = os.environ.get("LAPART_EXECUTOR_DB")
        source = os.environ.get("LAPART_GOVERNED_EXECUTOR_SRC")
        if database is None and source is None:
            # Same installation as launch-prime-mcp.sh, resolved per host.
            # Direct CLI entry does not inherit that launcher's environment.
            # Reuse existing storage only; never provision an implicit owner.
            installation = Path(__file__).resolve().parents[2]
            existing_db = installation / ".state/governed-executor/tasks.sqlite3"
            existing_source = installation / "governed-executor-runtime/src"
            if existing_db.is_file() and (existing_source / "lapart_executor").is_dir():
                return cls(existing_db, existing_source)
        if not database or not source:
            return cls(None, None, "CONFIGURATION_UNAVAILABLE")
        return cls(Path(database), Path(source))

    def _runtime(self):
        if self.executor_db is None or self.executor_source is None:
            raise RuntimeError(self.unavailable_reason or "CONFIGURATION_UNAVAILABLE")
        source = self.executor_source.resolve()
        package_root = source if (source / "lapart_executor").is_dir() else source / "src"
        if not (package_root / "lapart_executor").is_dir():
            raise RuntimeError("EXECUTOR_SOURCE_UNAVAILABLE")
        source_text = str(package_root)
        if source_text not in sys.path:
            sys.path.insert(0, source_text)
        service_module = importlib.import_module("lapart_executor.service")
        models_module = importlib.import_module("lapart_executor.models")
        adapter_module = importlib.import_module("lapart_executor.adapters.hermes")
        return (
            service_module.ExecutorService, models_module.TenantScope,
            adapter_module.HermesTrajectoryCapture,
        )

    def bootstrap(
        self,
        *,
        record: dict[str, Any],
        prompt: str,
        actual_lane: str,
        policy_digest: str,
        capability_digest: str,
        preset_digest: str,
        allowed_child_tools: tuple[str, ...],
    ) -> dict[str, Any]:
        if actual_lane not in ELIGIBLE_LANES:
            raise ValueError("governed session bootstrap requires an exact T3/T4 lane")
        if self.executor_db is None or self.executor_source is None:
            return {
                "status": "DEGRADED",
                "reason": self.unavailable_reason or "CONFIGURATION_UNAVAILABLE",
                "actual_lane": actual_lane,
            }
        job_id = str(record["job_id"])
        task_id = f"prime-task:{job_id}"
        run_id = f"prime-run:{job_id}"
        session_id = f"prime-session:{job_id}"
        route_digest = str(record["route"]["fingerprint"])
        project_id = _scope_id(str(record["project"]))
        expected_scope = {"organization_id": "personal", "project_id": project_id}
        budget = {
            "max_wall_seconds": int(record["limits"]["wall_seconds"]),
            "max_tool_calls": int(record["limits"]["max_iterations"]),
            "max_cost_usd": 0,
        }
        autonomy_budget = {
            **budget,
            "max_model_calls": max(2, int(record["limits"]["max_iterations"])),
            "tokens": 120_000,
        }
        envelope = {
            "task_id": task_id,
            "scope": expected_scope,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "requested_by": "prime-governed-bootstrap",
            "harness": "hermes",
            "intent": f"Prime coding handoff sha256:{_digest(prompt)}",
            "authority_scope": ["workspace:read", "workspace:propose-patch"],
            "risk_tier": "medium",
            "budget": budget,
            "workflow": {
                "workflow_id": f"workflow:{job_id}",
                "nodes": [{
                    "node_id": "prime-execution", "kind": "activity",
                    "timeout_seconds": budget["max_wall_seconds"],
                }],
                "entry_nodes": ["prime-execution"],
                "terminal_nodes": ["prime-execution"],
            },
            "termination_criteria": ["Prime supervisor reaches a terminal verified state"],
            "policy_revision": policy_digest,
        }
        try:
            ExecutorService, TenantScope, _capture_type = self._runtime()
            with ExecutorService(
                self.executor_db,
                scope=TenantScope(**expected_scope),
            ) as service:
                try:
                    current = service.status(task_id)
                except Exception as exc:
                    if type(exc).__name__ != "TaskNotFound":
                        raise
                    service.create(envelope)
                else:
                    existing = current.get("envelope", {})
                    identity = {
                        key: existing.get(key)
                        for key in ("task_id", "scope", "requested_by", "harness", "authority_scope", "policy_revision")
                    }
                    expected = {key: envelope[key] for key in identity}
                    if identity != expected:
                        raise GovernedSessionConflict("TASK_IDENTITY_CONFLICT")

                try:
                    run = service.autonomous_run(run_id)
                except Exception as exc:
                    if type(exc).__name__ != "RunNotFound":
                        raise
                    run = service.create_autonomous_run(
                        task_id,
                        run_id=run_id,
                        objective_digest=_digest({"prompt": prompt, "job_id": job_id}),
                        selected_lane="prime",
                        autonomy_budget=autonomy_budget,
                        model_route_fingerprint=route_digest,
                        prime_job_binding=job_id,
                    )
                expected_run = {
                    "task_id": task_id,
                    "scope": ["personal", project_id],
                    "selected_lane": "prime",
                    "model_route_fingerprint": route_digest,
                    "prime_job_binding": job_id,
                }
                if any(run.get(key) != value for key, value in expected_run.items()):
                    raise GovernedSessionConflict("RUN_IDENTITY_CONFLICT")

                try:
                    header = service.session_header(session_id)
                except Exception as exc:
                    if type(exc).__name__ != "SessionNotFound":
                        raise
                    header = service.create_session(
                        session_id=session_id,
                        task_id=task_id,
                        run_id=run_id,
                        model_route_fingerprint=route_digest,
                    )
                expected_header = {
                    "task_id": task_id,
                    "run_id": run_id,
                    "scope": ["personal", project_id],
                    "model_route_fingerprint": route_digest,
                }
                if any(header.get(key) != value for key, value in expected_header.items()):
                    raise GovernedSessionConflict("SESSION_IDENTITY_CONFLICT")

                service.append_session_event(
                    session_id,
                    event_type="system.context",
                    actor="prime-bridge",
                    payload={
                        "context_kind": "prime_session_handoff",
                        "prime_job_id": job_id,
                        "native_session_id": str(record["session_id"]),
                        "profile": str(record["profile"]),
                    },
                    event_id=f"prime-handoff:{job_id}",
                )
                authority = service.bind_session_runtime(
                    session_id,
                    actual_lane=actual_lane,
                    feature_state="active",
                    policy_digest=policy_digest,
                    capability_digest=capability_digest,
                    preset_digest=preset_digest,
                    allowed_child_tools=allowed_child_tools,
                )
                return {
                    "status": "READY",
                    "reason": "GOVERNED_SESSION_BOUND",
                    "actual_lane": actual_lane,
                    "task_id": task_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "authority_event_id": authority["event_id"],
                    "scope": expected_scope,
                    "native_session_id": str(record["session_id"]),
                    "prime_job_id": job_id,
                }
        except GovernedSessionConflict:
            raise
        except Exception as exc:
            conflict_names = {
                "IntegrityError", "InvalidTransition", "RunIdentityConflict",
                "SessionIdentityConflict", "ExecutorServiceError",
            }
            if type(exc).__name__ in conflict_names:
                raise GovernedSessionConflict(type(exc).__name__) from exc
            return {
                "status": "DEGRADED",
                "reason": f"BOOTSTRAP_UNAVAILABLE:{type(exc).__name__}",
                "actual_lane": actual_lane,
            }

    def open_session(self, receipt: dict[str, Any]) -> GovernedPrimeSession | None:
        """Open capture only for a successfully bound session; degraded mode stays free."""
        if receipt.get("status") != "READY":
            return None
        ExecutorService, TenantScope, Capture = self._runtime()
        service = ExecutorService(
            self.executor_db,
            scope=TenantScope(**dict(receipt["scope"])),
        )
        try:
            capture = Capture(
                service,
                session_id=str(receipt["session_id"]),
                actual_lane=str(receipt["actual_lane"]),
                feature_state="active",
                native_session_id=str(receipt["native_session_id"]),
                prime_job_id=str(receipt["prime_job_id"]),
                actor="prime",
            )
            handle = GovernedPrimeSession(
                service, capture,
                task_id=str(receipt["task_id"]), run_id=str(receipt["run_id"]),
                session_id=str(receipt["session_id"]), actual_lane=str(receipt["actual_lane"]),
            )
            handle.lifecycle("starting", reason="Prime governed session admitted")
            return handle
        except Exception:
            service.close()
            raise


__all__ = [
    "ELIGIBLE_LANES", "GovernedPrimeSession", "GovernedSessionBridge",
    "GovernedSessionConflict",
]
