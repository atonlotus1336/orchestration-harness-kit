from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

from prime_bridge.adaptive_parallel_activation import (
    _evidence_hmac_sha256,
    source_binding,
)
from prime_bridge.coding_execution import dispatch_coding_execution
from prime_bridge.governed_session_bridge import GovernedPrimeSession
from prime_bridge.mcp_server import PrimeToolService


GOVERNED_SOURCE = Path(__file__).resolve().parents[3] / "governed-executor-runtime" / "src"
if str(GOVERNED_SOURCE) not in sys.path:
    sys.path.insert(0, str(GOVERNED_SOURCE))


def _digest(value) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _admission(count: int = 2):
    return {
        "schema_version": "2.0", "layer": "execution",
        "policy_sha256": "a" * 64, "desired_count": count,
        "admitted_count": count, "running_count": 0,
        "observed_ready_count": count, "limiting_reasons": ["ADMITTED"],
        "decided_at": "2026-09-10T12:00:00Z", "trusted_feedback_sha256": None,
    }


class _Capture:
    def governance(self, *_args, **_kwargs):
        return None

    def close(self):
        return {"status": "PASS"}


class _Coordinator:
    def binding(self, child_session_id):
        return {"preset_digest": "b" * 64, "child_session_id": child_session_id}


class _ProviderController:
    def __init__(self, patches, *, result_hook=None):
        self.coordinator = _Coordinator()
        self.patches = patches
        self.result_hook = result_hook
        self.isolation = {}
        self.calls = []

    def discover(self, **arguments):
        self.calls.append(("discover", dict(arguments)))
        return SimpleNamespace(to_dict=lambda: {
            "status": "PASS", "code": "PROVIDER_SCHEMA_EXPOSED",
            "actual_lane": arguments["actual_lane"], "preset_id": arguments["preset_id"],
            "provider_kind": arguments["provider_kind"],
            "operations": ["start", "result"], "start_modes": ["spawn"],
            "provider_calls": 0, "session_writes": 0,
        })

    def start(self, child_session_id, **arguments):
        self.calls.append(("start", child_session_id))
        self.isolation[child_session_id] = arguments["request"]["isolation"]
        return SimpleNamespace(
            status="PASS", code="PROVIDER_BOUND", provider_calls=1,
            resume_token=f"resume-{child_session_id}",
        )

    def result(self, child_session_id, **_arguments):
        self.calls.append(("result", child_session_id))
        if self.result_hook is not None:
            self.result_hook(child_session_id)
        isolation = self.isolation[child_session_id]
        patch = self.patches[child_session_id]
        return SimpleNamespace(
            status="PASS", code="PROVIDER_OPERATION_ACCEPTED", provider_calls=1,
            provider_payload={
                "schema_version": "isolated-coding-proposal-v1",
                "child_session_id": child_session_id,
                "base_identity": isolation["base_identity"],
                "workspace_identity": isolation["workspace_identity"],
                "changed_paths": [patch["path"]], "patch": patch,
                "patch_sha256": _digest(patch), "local_test_status": "PASS",
            },
        )


class _GovernedService:
    def __init__(self, controller):
        self.child_providers = controller
        self.snapshots = []
        self.diagnostics = []

    def record_workspace_snapshot(self, session_id, **arguments):
        self.snapshots.append((session_id, arguments))
        return {"status": "PASS"}

    def record_workspace_diagnostics(self, session_id, **arguments):
        self.diagnostics.append((session_id, arguments))
        return {"status": "PASS"}

    def autonomous_run(self, _run_id):
        return {"lifecycle_state": "running"}

    def close(self):
        return None


def _units():
    return [
        {
            "child_session_id": "child-a", "provider_kind": "prime",
            "preset_id": "coding", "start_mode": "spawn",
            "operation_id": "start-a", "result_operation_id": "result-a",
            "allowed_paths": ["a.py"],
        },
        {
            "child_session_id": "child-b", "provider_kind": "prime",
            "preset_id": "coding", "start_mode": "spawn",
            "operation_id": "start-b", "result_operation_id": "result-b",
            "allowed_paths": ["b.py"],
        },
    ]


class _AdaptiveHostService:
    profile = "stub-profile"

    def __init__(
        self, workspace: Path, *, conflict: bool = False,
        out_of_scope: bool = False, stale_parent: bool = False,
    ):
        self.workspace = workspace
        patches = {
            "child-a": {
                "path": "outside.py" if out_of_scope else "a.py",
                "content": "VALUE = 1\n",
            },
            "child-b": {"path": "b.py", "content": "VALUE = 2\n" if conflict else "VALUE = 1\n"},
        }
        stale_written = False

        def mutate_parent(_child_session_id):
            nonlocal stale_written
            if stale_parent and not stale_written:
                (workspace / "a.py").write_text("EXTERNAL CHANGE\n", encoding="utf-8")
                stale_written = True

        governed = _GovernedService(_ProviderController(patches, result_hook=mutate_parent))
        self.handle = GovernedPrimeSession(
            governed, _Capture(), task_id="task", run_id="run",
            session_id="parent-session", actual_lane="T3_PRIME_OMP",
        )
        self.apply_order = []

    def _apply(self, proposal):
        patch = proposal["patch"]
        path = self.workspace / patch["path"]
        previous = path.read_bytes() if path.exists() else None
        path.write_text(patch["content"], encoding="utf-8")
        self.apply_order.append(proposal["child_session_id"])
        return {
            "status": "VERIFIED_OK", "applied": [patch["path"]],
            "rollback_token": (patch["path"], previous),
        }

    def _rollback(self, token):
        relative, previous = token
        path = self.workspace / relative
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)
        return {"status": "ROLLED_BACK", "path": relative}

    def _aggregate(self, _proposals):
        values = {
            (self.workspace / name).read_text(encoding="utf-8")
            for name in ("a.py", "b.py")
        }
        return {
            "status": "VERIFIED_OK" if len(values) == 1 else "VERIFIED_FAILED",
            "oracle": "matching-values",
        }

    async def execute_adaptive_coding_plan(self, arguments):
        self.arguments = arguments
        result = self.handle.execute_adaptive_coding_batch(
            admission=arguments["adaptive_admission"], workspace=self.workspace,
            units=_units(), apply_proposal=self._apply,
            rollback_proposal=self._rollback, verify_aggregate=self._aggregate,
        )
        complete = result["status"] == "PASS" and result["integration_authorized"]
        return {
            "outcome": "complete" if complete else "blocked",
            "reason": result["reason"],
            "parent": {"job_id": "p" * 32, "status": "completed"},
            "omp_children": [], "integration_authorized": complete,
            "host_apply": {"status": "VERIFIED_OK" if complete else "VERIFIED_FAILED"},
            "proposal_count": result["proposal_count"],
            "provider_calls": result["provider_calls"], "model_requests": 0,
            "provider_capabilities": result["capabilities"],
            "integration_sequence": result["integration_sequence"],
            "aggregate_verification": result["aggregate_verification"],
        }


def test_real_dispatch_routes_actual_t3_through_child_owner_and_serial_parent_integration(tmp_path):
    service = _AdaptiveHostService(tmp_path)
    result = asyncio.run(dispatch_coding_execution(
        service, task="adaptive coding", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts",
        declared_paths=["a.py", "b.py"], execution_lane="T3_PRIME_OMP",
        execution_value={"recommended_lane": "T2_OMP_LITE", "actual_lane": "T3_PRIME_OMP"},
        adaptive_admission=_admission(),
    ))
    assert result["outcome"] == "complete"
    assert result["integration_authorized"] is True
    assert result["execution_lane"] == "T3_PRIME_OMP"
    assert result["adaptive_host_routing"]["status"] == "ROUTED"
    assert service.apply_order == ["child-a", "child-b"]
    assert result["proposal_count"] == 2
    assert result["aggregate_verification"]["status"] == "VERIFIED_OK"


def test_individually_passing_semantic_conflict_fails_aggregate_and_rolls_back(tmp_path):
    (tmp_path / "a.py").write_text("ORIGINAL_A\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("ORIGINAL_B\n", encoding="utf-8")
    service = _AdaptiveHostService(tmp_path, conflict=True)
    result = asyncio.run(dispatch_coding_execution(
        service, task="adaptive coding", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts",
        declared_paths=["a.py", "b.py"], execution_lane="T3_PRIME_OMP",
        adaptive_admission=_admission(),
    ))
    assert result["outcome"] == "blocked"
    assert result["integration_authorized"] is False
    assert result["reason"] == "AGGREGATE_VERIFICATION_FAILED"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "ORIGINAL_A\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "ORIGINAL_B\n"


def test_missing_native_adaptive_control_returns_explicit_serial_fallback(tmp_path):
    class Unsupported:
        profile = "stub-profile"

    result = asyncio.run(dispatch_coding_execution(
        Unsupported(), task="adaptive coding", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts",
        declared_paths=["a.py"], execution_lane="T3_PRIME_OMP",
        adaptive_admission=_admission(1),
    ))
    assert result["outcome"] == "fallback"
    assert result["reason"] == "ADAPTIVE_HOST_CONTROL_UNSUPPORTED"
    assert result["fallback_path"] == "serial"
    assert result["provider_calls"] == 0
    assert result["proposal_count"] == 0
    assert result["integration_authorized"] is False


def test_stale_parent_base_is_held_before_any_proposal_is_applied(tmp_path):
    (tmp_path / "a.py").write_text("ORIGINAL_A\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("ORIGINAL_B\n", encoding="utf-8")
    service = _AdaptiveHostService(tmp_path, stale_parent=True)
    result = asyncio.run(dispatch_coding_execution(
        service, task="adaptive coding", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts",
        declared_paths=["a.py", "b.py"], execution_lane="T3_PRIME_OMP",
        adaptive_admission=_admission(),
    ))
    assert result["outcome"] == "blocked"
    assert result["reason"] == "STALE_PARENT_BASE"
    assert result["proposal_count"] == 2
    assert result["integration_authorized"] is False
    assert service.apply_order == []
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "EXTERNAL CHANGE\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "ORIGINAL_B\n"


def test_out_of_scope_provider_proposal_cannot_reach_parent_apply(tmp_path):
    service = _AdaptiveHostService(tmp_path, out_of_scope=True)
    result = asyncio.run(dispatch_coding_execution(
        service, task="adaptive coding", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts",
        declared_paths=["a.py", "b.py"], execution_lane="T3_PRIME_OMP",
        adaptive_admission=_admission(),
    ))
    assert result["outcome"] == "blocked"
    assert result["reason"] == "CODING_SCOPE_VIOLATION"
    assert result["integration_authorized"] is False
    assert result["proposal_count"] == 0
    assert service.apply_order == []
    assert not (tmp_path / "outside.py").exists()


def test_t2_keeps_the_existing_single_recovery_path_when_adaptive_facts_are_present(tmp_path):
    class Lite:
        async def execute_omp_lite(self, arguments):
            self.arguments = arguments
            return {
                "outcome": "complete", "reason": "OMP_LITE_COMPLETE",
                "parent": {"job_id": "p" * 32, "status": "completed"},
                "omp_children": [{"child_job_id": "c" * 32, "status": "completed"}],
                "integration_authorized": True,
                "host_apply": {"status": "VERIFIED_OK"},
            }

    service = Lite()
    result = asyncio.run(dispatch_coding_execution(
        service, task="small fix", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts",
        declared_paths=["a.py"], execution_lane="T2_OMP_LITE",
        adaptive_admission=_admission(1),
    ))
    assert result["outcome"] == "complete"
    assert result["adaptive_host_routing"] == {
        "status": "BYPASSED", "reason": "EXISTING_LANE_PRESERVED",
    }
    assert "adaptive_admission" not in service.arguments


HARNESS_ROOT = Path(__file__).resolve().parents[2]


class _LiveProviderController(_ProviderController):
    def __init__(self, workspace: Path):
        super().__init__({})
        self.workspace = workspace
        self.task_packets = {}

    def start(self, child_session_id, **arguments):
        self.calls.append(("start", child_session_id))
        self.isolation[child_session_id] = arguments["request"]["isolation"]
        self.task_packets[child_session_id] = arguments["request"]["task_packet"]
        return SimpleNamespace(
            status="PASS", code="PROVIDER_BOUND", provider_calls=1,
            resume_token=f"resume-{child_session_id}",
        )

    def result(self, child_session_id, **_arguments):
        self.calls.append(("result", child_session_id))
        isolation = self.isolation[child_session_id]
        relative = self.task_packets[child_session_id]["allowed_paths"][0]
        target = self.workspace / relative
        before = target.read_bytes() if target.is_file() else b""
        after = b"VALUE = 1\n"
        manifest = [{
            "path": relative,
            "operation": "replace" if target.is_file() else "create",
            "before_hash": hashlib.sha256(before).hexdigest(),
            "after_bytes_b64": base64.b64encode(after).decode("ascii"),
            "after_hash": hashlib.sha256(after).hexdigest(),
        }]
        omp_patch = {
            "manifest": manifest,
            "patch_hash": _digest(manifest),
            "source_hash": hashlib.sha256(before).hexdigest(),
            "final_tree_hash": hashlib.sha256(after).hexdigest(),
        }
        wrapper = {"omp_patch": omp_patch, "capability_receipt": "c" * 64}
        return SimpleNamespace(
            status="PASS", code="PROVIDER_OPERATION_ACCEPTED", provider_calls=1,
            provider_payload={
                "schema_version": "isolated-coding-proposal-v1",
                "child_session_id": child_session_id,
                "base_identity": isolation["base_identity"],
                "workspace_identity": isolation["workspace_identity"],
                "changed_paths": [relative], "patch": wrapper,
                "patch_sha256": _digest(wrapper), "local_test_status": "PASS",
            },
        )


class _LiveGovernedService(_GovernedService):
    def __init__(self, workspace: Path):
        super().__init__(_LiveProviderController(workspace))
        self.run_state = "running"

    def autonomous_run(self, _run_id):
        return {"lifecycle_state": self.run_state}

    def transition_autonomous_run(self, _run_id, *, target, reason):
        self.run_state = target
        return {"lifecycle_state": target, "reason": reason}


class _StubGovernedBridge:
    def __init__(self, workspace: Path):
        self.governed = _LiveGovernedService(workspace)
        self.bootstrap_calls = 0

    def bootstrap(self, *, record, actual_lane, **_arguments):
        self.bootstrap_calls += 1
        return {
            "status": "READY", "reason": "TEST_BOUND", "actual_lane": actual_lane,
            "task_id": f"task-{record['job_id']}",
            "run_id": f"run-{record['job_id']}",
            "session_id": f"session-{record['job_id']}",
            "native_session_id": record["session_id"],
            "prime_job_id": record["job_id"],
        }

    def open_session(self, receipt):
        return GovernedPrimeSession(
            self.governed, _Capture(), task_id=receipt["task_id"],
            run_id=receipt["run_id"], session_id=receipt["session_id"],
            actual_lane=receipt["actual_lane"],
        )


def _write_profile(path: Path):
    path.write_text(yaml.safe_dump({
        "model": {
            "provider": "custom:ollama-cloud-local",
            "default": "deepseek-v4-flash:cloud",
        },
        "providers": {"ollama-cloud-local": {}},
        "mcp_servers": {}, "memory": {}, "toolsets": ["hermes-cli"],
    }), encoding="utf-8")


def _activate_test_execution_grant(
    service: PrimeToolService, policy_path: Path, qualification_path: Path,
    *, key: bytes,
):
    policy = json.loads(
        (HARNESS_ROOT / "config/adaptive-parallel-policy.json").read_text(encoding="utf-8")
    )
    expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    policy["activation_state"] = "production"
    policy["strategy"] = "fixed_two"
    policy["execution"].update({
        "enabled": True, "qualification_state": "production_qualified",
        "initial_ceiling": 2, "qualified_ceiling": 2, "desired_ceiling": 2,
    })
    policy["grants"]["execution_parallelism"].update({
        "enabled": True, "state": "production_qualified",
        "qualification_reference": "0" * 64, "expiry": expiry,
    })
    policy_path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    binding = source_binding(policy_path)
    evidence = {
        "schema_version": "2.0", "harness": "codex", "profile": "home",
        "model_route_sha256": service.resolved_route()["fingerprint"],
        "workload_class": "coding", "capability": "isolated_coding",
        "s_pipeline": None, "expires_at": expiry,
        "budgets": {"max_calls": 20, "max_tokens": 120000, "max_wall_seconds": 600, "max_cost": 0},
        "provider_free_passes": 10, "live_trials": 50, "live_failures": 0,
        "soak_hours": 72, "lineages": 50, "multilineage_capacity": 2,
        "rollback_exercised": True, "expiry_exercised": True, "scope": "wider",
        "policy_sha256": binding["policy_sha256"], "layer_ceiling": 2,
        "matched_pairs": 50,
        "quality": {
            "mandatory_recall": 1.0, "precision": 1.0, "schema_validity": 1.0,
            "answerable_refusal_rate": 0.0, "p95_latency_ratio": 1.0,
        },
        "accounting": {
            "permission_bypasses": 0, "lost_work": 0, "duplicated_work": 0,
            "unsafe_publications": 0, "unaccounted_usage": 0,
            "missed_seeded_critical_defects": 0,
        },
        "qualification_predecessor_sha256": "d" * 64,
        "limited_predecessor_sha256": "d" * 64,
        "authority_hmac_sha256": "",
    }
    evidence["authority_hmac_sha256"] = _evidence_hmac_sha256(evidence, key)
    record = {
        **evidence, "layer": "execution_parallelism",
        "qualification_state": "production_qualified",
        "source_binding": binding,
        "qualified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    reference = _digest(record)
    qualification_path.write_text(json.dumps({
        "schema_version": "2.1", "records": {reference: record},
        "current": {"test-execution": reference},
    }, indent=2), encoding="utf-8")
    policy["grants"]["execution_parallelism"]["qualification_reference"] = reference
    policy_path.write_text(json.dumps(policy, indent=2), encoding="utf-8")


def _real_service(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = tmp_path / "config.yaml"
    _write_profile(profile)
    adaptive_policy = tmp_path / "adaptive-policy.json"
    qualification = tmp_path / "qualification.json"
    bridge = _StubGovernedBridge(workspace)
    now = lambda: datetime.now(timezone.utc).isoformat()
    key = b"test-adaptive-authority-key"
    service = PrimeToolService(
        profile="home", mode="prime-default",
        policy_path=HARNESS_ROOT / "config/prime-profile-policy.json",
        ledger_path=tmp_path / "jobs.sqlite3", profile_config=profile,
        allowed_roots=[tmp_path], governed_session_bridge=bridge,
        adaptive_policy_path=adaptive_policy,
        adaptive_qualification_path=qualification,
        adaptive_feedback_root=tmp_path / "feedback",
        adaptive_authority_key=key, adaptive_host_capacity=4,
        resource_provider=lambda: {
            "status": "VERIFIED", "cpu_percent": 1, "memory_percent": 1,
            "sampled_at": now(), "source": "controlled-test",
        },
    )
    _activate_test_execution_grant(service, adaptive_policy, qualification, key=key)
    return service, bridge, workspace


def test_actual_prime_service_owns_adaptive_batch_and_verifies_aggregate(tmp_path):
    service, bridge, workspace = _real_service(tmp_path)
    admission = service.prepare_adaptive_coding_admission(
        actual_lane="T3_PRIME_OMP", task="create matching constants",
        workspace=workspace, project="demo", harness="codex",
        declared_paths=["a.py", "b.py"],
    )
    assert admission is not None and admission["admitted_count"] == 2
    result = asyncio.run(dispatch_coding_execution(
        service, task="create matching constants", workspace=workspace,
        project="demo", harness="codex", receipt_root=tmp_path / "receipts",
        declared_paths=["a.py", "b.py"], execution_lane="T3_PRIME_OMP",
        execution_value={"recommended_lane": "T3_PRIME_OMP", "actual_lane": "T3_PRIME_OMP"},
        adaptive_admission=admission,
    ))
    assert result["outcome"] == "complete"
    assert result["integration_authorized"] is True
    assert result["aggregate_verification"]["aggregate"] is True
    assert result["proposal_count"] == 2 and result["provider_calls"] == 4
    assert bridge.bootstrap_calls == 1
    assert (workspace / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (workspace / "b.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_actual_prime_service_rejects_caller_minted_admission_before_bootstrap(tmp_path):
    service, bridge, workspace = _real_service(tmp_path)
    result = asyncio.run(dispatch_coding_execution(
        service, task="caller minted", workspace=workspace,
        project="demo", harness="codex", receipt_root=tmp_path / "receipts",
        declared_paths=["a.py", "b.py"], execution_lane="T3_PRIME_OMP",
        adaptive_admission=_admission(),
    ))
    assert result["outcome"] == "blocked"
    assert result["reason"] == "ADAPTIVE_ADMISSION_UNTRUSTED"
    assert result["integration_authorized"] is False
    assert result["provider_calls"] == 0 and bridge.bootstrap_calls == 0
    assert not (workspace / "a.py").exists()
