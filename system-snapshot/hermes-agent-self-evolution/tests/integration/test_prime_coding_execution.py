import asyncio
import json
from pathlib import Path

import pytest

from prime_bridge.coding_execution import (
    _child_proposal_count,
    dispatch_coding_execution,
    normalize_receipt,
    task_packet_public,
)
from prime_bridge.mcp_server import (
    PRIME_REVIEW_PATCH_LIMIT,
    RLM_PROMOTION_RECEIPT,
    PrimeToolError,
    PrimeToolService,
    _compact_omp_review_packet,
    _aggregate_coding_usage,
    _coding_call_accounting,
    _external_test_review_note,
    _host_verification_command,
    _prime_patch_review,
    _prime_rlm_review,
    _rlm_promotion_ready,
    _worker_repair_context,
)
from prime_bridge.omp_task_packet import compile_atomic_task_packet
from prime_bridge.omp_service import OmpServiceError, bounded_repair_turns
from prime_bridge.usage_ledger import UsageLedger


def _task_packet(objective: str = "update one docstring") -> dict:
    return task_packet_public(
        compile_atomic_task_packet(objective=objective, path="demo.py")
    )


def test_rlm_review_requires_exact_ordered_hash_batch_and_decision_vocabulary():
    hashes = ["a" * 64, "b" * 64]
    accepted = json.dumps({
        "decision": "accepted", "patch_hashes": hashes,
        "reason": "both bounded excerpts are safe", "risk_findings": [],
    })
    assert _prime_rlm_review(accepted, hashes)["decision"] == "accepted"
    assert _prime_rlm_review(accepted, list(reversed(hashes)))["decision"] == "invalid"
    assert _prime_rlm_review(accepted.replace('"accepted"', '"accept"'), hashes)["decision"] == "invalid"


def test_rlm_runtime_reads_phase13_promotion_without_rewriting_phase12():
    assert RLM_PROMOTION_RECEIPT.name == "phase13-rlm-live-matched.json"


def test_rlm_promotion_gate_requires_exact_live_pass_receipt(tmp_path):
    receipt = tmp_path / "promotion.json"
    assert _rlm_promotion_ready(receipt) is False
    receipt.write_text(json.dumps({
        "trial_class": "live_matched", "status": "HOLD", "activation": False,
    }), encoding="utf-8")
    assert _rlm_promotion_ready(receipt) is False
    receipt.write_text(json.dumps({
        "trial_class": "live_matched", "status": "PASS", "activation": True,
    }), encoding="utf-8")
    assert _rlm_promotion_ready(receipt) is True


class _RecordingLedger:
    def __init__(self):
        self.notes = []

    def note(self, job_id, event, payload):
        self.notes.append((job_id, event, payload))


def test_omp_lite_budget_reserves_no_prime_provider_call():
    assert bounded_repair_turns({"provider_calls": 1, "repair_turns": 1, "prime_required": False}) == 0
    assert bounded_repair_turns({"provider_calls": 2, "repair_turns": 1, "prime_required": False}) == 1


def test_phase32_t3_usage_aggregates_prime_and_omp_without_zero_filling():
    aggregate, components = _aggregate_coding_usage(
        {"model_usage": {
            "input_units": 20, "output_units": 3,
            "cache_read_units": 5, "cache_write_units": 1, "total_units": 29,
        }},
        {
            "input_tokens": 10, "output_tokens": 2,
            "cache_read_tokens": 1, "cache_write_tokens": 0, "total_tokens": 13,
        },
    )
    assert aggregate == {
        "input_tokens": 30, "output_tokens": 5,
        "cache_read_tokens": 6, "cache_write_tokens": 1, "total_tokens": 42,
    }
    assert components["complete"] is True
    missing, incomplete = _aggregate_coding_usage({}, {
        "input_tokens": 10, "output_tokens": 2,
        "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": 12,
    })
    assert missing is None
    assert incomplete["complete"] is False


def test_failed_route_call_accounting_uses_observed_prime_and_omp_attempts():
    accounting = _coding_call_accounting(
        {"status": "failed", "model_usage": {
            "input_units": 20, "output_units": 3, "total_units": 23,
        }},
        [{"status": "failed", "record": {"sdk_evidence": {
            "provider_calls": 2, "model_requests": 7,
        }}}],
    )
    assert accounting == {
        "provider_calls": 3,
        "model_requests": 8,
        "provider_call_accounting_complete": True,
        "provider_call_components": {"prime": 1, "omp": 2},
    }


def test_canonical_receipt_source_preserves_call_accounting_detail() -> None:
    source = (Path(__file__).resolve().parents[2] / "prime_bridge/coding_execution.py").read_text(encoding="utf-8")
    assert '"provider_call_accounting_complete": pipeline.get("provider_call_accounting_complete")' in source
    assert '"provider_call_components": pipeline.get("provider_call_components")' in source


def test_automatic_omp_failure_exposes_redacted_child_error(monkeypatch):
    from prime_bridge import mcp_server

    observed = {}

    class Broker:
        def __init__(self, *_args, **_kwargs):
            pass

        async def handle(self, _request):
            observed.update(_request)
            return {"status": "ok", "result": {"child_omp_job_id": "child-1"}}

    class Ledger(_RecordingLedger):
        def child(self, child_id):
            assert child_id == "child-1"
            return {
                "status": "failed",
                "record": {
                    "error_code": "SIDECAR_PROTOCOL_FAILURE",
                    "error": "model output must not be exposed by the parent error",
                },
            }

    monkeypatch.setattr(mcp_server, "CapabilityBroker", Broker)
    service = object.__new__(PrimeToolService)
    service.profile = "workspace"
    service.ledger = Ledger()
    service.capability_policy = object()
    service.capability_ledger = object()
    service.capability_adapter = object()
    service.learning_service = type("Learning", (), {"call": None})()
    service.omp_service = type(
        "Omp", (), {"call": None, "result_for": lambda _self, _id: None},
    )()

    callback = service._coding_before_worker(
        "replace a bounded fixture", compile_atomic_task_packet(
            objective="replace a bounded fixture", path="fixture.txt",
        ),
        repair_context="UNTRUSTED_TEST_DIAGNOSTIC. FAILED test_value",
    )
    with pytest.raises(PrimeToolError) as raised:
        asyncio.run(callback({"job_id": "b" * 32}, "bounded prompt"))

    message = str(raised.value)
    assert "error_code=SIDECAR_PROTOCOL_FAILURE" in message
    assert "reason_sha256=" in message
    assert "model output must not be exposed" not in message
    assert "FAILED test_value" in observed["arguments"]["prompt"]


def test_worker_repair_context_accepts_only_one_validated_test_section():
    projection = {
        "profile": "governed", "surface": "task_packet",
        "sections": [{
            "category": "tool_output",
            "content": "UNTRUSTED_TEST_DIAGNOSTIC. FAILED test_value",
            "required": True, "priority": 100, "source_type": "verification",
        }],
    }
    assert _worker_repair_context(projection).endswith("FAILED test_value")
    assert _worker_repair_context({**projection, "sections": []}) is None
    with pytest.raises(PrimeToolError, match="ambiguous"):
        _worker_repair_context({**projection, "sections": projection["sections"] * 2})


def test_omp_lite_task_cancellation_terminalizes_parent(tmp_path, monkeypatch):
    from prime_bridge import mcp_server

    entered = asyncio.Event()
    blocked = asyncio.Event()

    class Ledger:
        def __init__(self):
            self.value = {"job_id": "b" * 32, "status": "created"}

        def transition(self, _job_id, status, **fields):
            self.value = {**self.value, "status": status, **fields}
            return dict(self.value)

        def note(self, *_args, **_kwargs):
            return None

        def get(self, _job_id):
            return dict(self.value)

        def children(self, _job_id):
            return []

    class BlockingBroker:
        def __init__(self, *_args, **_kwargs):
            pass

        async def handle(self, _request):
            entered.set()
            await blocked.wait()

    class Handler:
        async def call(self, _request):
            return None

    monkeypatch.setattr(mcp_server, "CapabilityBroker", BlockingBroker)
    service = object.__new__(PrimeToolService)
    service.profile = "home"
    service.ledger = Ledger()
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, None)
    service.capability_policy = object()
    service.capability_ledger = object()
    service.capability_adapter = object()
    service.learning_service = Handler()
    service.omp_service = Handler()

    async def run_and_cancel():
        task = asyncio.create_task(service.execute_omp_lite({
            "task": "update one docstring", "harness": "codex",
            "workspace": str(tmp_path), "project": "demo", "session_id": "session-1",
            "task_packet": task_packet_public(compile_atomic_task_packet(
                objective="update one docstring", path="demo.py",
                semantic_assertions=("contains:demo.py:docstring",))), "wait": True,
        }))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())
    assert service.ledger.value["status"] == "cancelled"
    assert service.ledger.value["last_error"] == "OMP-lite task cancelled"


@pytest.mark.parametrize("value, expected", [(2, 2), (0, 0), (-1, 0), (True, 0), ("2", 0), (None, 0)])
def test_child_proposal_count_is_typed(value, expected):
    child = {"record": {"sdk_evidence": {"proposal_count": value}}}
    assert _child_proposal_count(child) == expected


def test_compact_review_packet_excludes_proposed_bytes_and_bounds_patch_text():
    patch_text = "x" * (PRIME_REVIEW_PATCH_LIMIT + 17)
    packet = _compact_omp_review_packet({
        "child_omp_job_id": "a" * 32,
        "status": "completed",
        "sdk_evidence": {"proposal_count": 1},
        "patch": {
            "patch_hash": "b" * 64,
            "source_hash": "c" * 64,
            "final_tree_hash": "d" * 64,
            "changed_paths": ["demo.py"],
            "patch": patch_text,
            "manifest": [{
                "path": "demo.py", "operation": "replace",
                "before_hash": "e" * 64, "after_hash": "f" * 64,
                "after_bytes_b64": "must-not-reach-prime",
            }],
        },
    }, task_packet_hash="1" * 64, patch_guard={"status": "accepted"})
    encoded = json.dumps(packet, sort_keys=True)
    assert "must-not-reach-prime" not in encoded
    assert len(packet["patch"]["text_excerpt"]) == PRIME_REVIEW_PATCH_LIMIT
    assert packet["patch"]["text_length"] == len(patch_text)
    assert packet["patch"]["text_truncated"] is True
    assert len(packet["patch"]["text_sha256"]) == 64 and isinstance(packet["patch"]["text_sha256"], str)


def test_prime_review_accepts_valid_json_before_transport_suffix():
    child = {
        "record": {
            "patch_hash": "a" * 64,
            "patch_guard": {"status": "accepted"},
        },
    }
    summary = (
        '{"decision":"accepted","patch_hash":"' + "a" * 64
        + '","reason":"bounded and safe","risk_findings":[]}</parameter>'
    )
    assert _prime_patch_review(summary, child)["decision"] == "accepted"


class Service:
    def __init__(self, parent, children, usage=None):
        self.parent = parent
        self.children = children
        self.usage = usage

    async def execute_coding_plan(self, arguments):
        self.arguments = arguments
        compliant = self.parent["status"] == "completed" and len(self.children) == 1 and self.children[0]["status"] == "completed"
        return {
            "outcome": "complete" if compliant else "blocked",
            "reason": "GOVERNED_ROUTE_COMPLETE" if compliant else "PRIME_OMP_CONTRACT_UNSATISFIED",
            "parent": self.parent,
            "omp_children": self.children,
            "integration_authorized": compliant,
            "usage": self.usage,
        }


def test_dispatch_requires_one_completed_omp_child(tmp_path):
    child = {"kind": "omp_worker", "child_job_id": "a" * 32, "status": "completed"}
    service = Service({"job_id": "b" * 32, "status": "completed"}, [child])
    result = asyncio.run(dispatch_coding_execution(
        service, task="execute plan 4", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts", declared_paths=["demo.py"],
    ))
    assert result["outcome"] == "complete"
    assert result["integration_authorized"] is True
    assert result["native_fallback_authorized"] is False
    assert service.arguments["task"] == "execute plan 4"
    assert json.loads(Path(result["receipt_path"]).read_text())["prime_active"] is True


@pytest.mark.parametrize(
    ("execution_lane", "durability_required"),
    [("T3_PRIME_OMP", True), ("T4_AUTONOMOUS", False)],
)
def test_governed_dispatch_persists_step_and_stage_timings(
    tmp_path, execution_lane, durability_required,
):
    child = {"kind": "omp_worker", "child_job_id": "a" * 32, "status": "completed"}
    service = Service({"job_id": "b" * 32, "status": "completed"}, [child])
    usage = UsageLedger(tmp_path / "usage.sqlite3")
    result = asyncio.run(dispatch_coding_execution(
        service, task="execute durable plan", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts", declared_paths=["demo.py"],
        execution_lane=execution_lane, durability_required=durability_required,
        usage_ledger=usage,
    ))
    step = json.loads(Path(result["durable_step"]["checkpoint_path"]).read_text())
    assert step["status"] == "completed"
    assert step["pending_write_status"] == "committed"
    assert step["resumable"] is False
    assert result["durable_step"]["model_calls"] == 0
    assert {"routing", "handoff", "persistence"}.issubset(result["stage_timings_ms"])
    assert usage.history()[0]["stage_timings_ms"] == result["stage_timings_ms"]


def test_dispatch_receipt_preserves_only_content_free_usage(tmp_path):
    child = {"kind": "omp_worker", "child_job_id": "a" * 32, "status": "completed"}
    usage = {"input_tokens": 10, "output_tokens": 4, "cache_read_tokens": 2,
             "cache_write_tokens": 1, "total_tokens": 14, "prompt": "secret"}
    result = asyncio.run(dispatch_coding_execution(
        Service({"job_id": "b" * 32, "status": "completed"}, [child], usage),
        task="execute plan 4", workspace=tmp_path, project="demo", harness="codex",
        receipt_root=tmp_path / "receipts", declared_paths=["demo.py"],
    ))
    assert result["usage"] == {key: usage[key] for key in (
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"
    )}
    assert "secret" not in json.dumps(result)


def test_dispatch_blocks_when_prime_skips_omp(tmp_path):
    service = Service({"job_id": "b" * 32, "status": "completed"}, [])
    result = asyncio.run(dispatch_coding_execution(
        service, task="execute plan", workspace=tmp_path, project="demo",
        harness="claude-code", receipt_root=tmp_path / "receipts", declared_paths=["demo.py"],
    ))
    assert result["outcome"] == "blocked"
    assert result["native_fallback_authorized"] is False


def test_dispatch_records_automatic_unavailable_fallback(tmp_path):
    class Unavailable:
        async def execute_coding_plan(self, _arguments):
            raise RuntimeError("offline")

    result = asyncio.run(dispatch_coding_execution(
        Unavailable(), task="execute plan", workspace=tmp_path, project="demo",
        harness="hermes", receipt_root=tmp_path / "receipts", declared_paths=["demo.py"],
    ))
    assert result["outcome"] == "fallback"
    assert result["native_fallback_authorized"] is True
    assert result["reason"] == "PRIME_UNAVAILABLE:RuntimeError"


def test_dispatch_uses_only_canonical_service_contract(tmp_path):
    child = {"kind": "omp_worker", "child_job_id": "a" * 32, "status": "completed"}

    class Deterministic(Service):
        async def delegate(self, _arguments):
            raise AssertionError("legacy delegate must not be used")

    service = Deterministic({"job_id": "b" * 32, "status": "completed"}, [child])
    result = asyncio.run(dispatch_coding_execution(
        service, task="execute plan", workspace=tmp_path, project="demo",
        harness="hermes", receipt_root=tmp_path / "receipts", declared_paths=["demo.py"],
    ))
    assert result["outcome"] == "complete"
    assert service.arguments["task"] == "execute plan"


@pytest.mark.parametrize("harness", ["hermes", "codex", "claude-code", "moa"])
def test_dispatch_propagates_explicit_recovery_contract_for_every_harness(tmp_path, harness):
    class Recovered:
        profile = "gpt-5.4-codex"

        async def execute_coding_plan(self, arguments):
            assert arguments["unattended"] is True
            assert arguments["verified_native_recovery"] is True
            assert arguments["fail_closed"] is False
            return {
                "outcome": "fallback", "reason": "SIDECAR_PROTOCOL_FAILURE",
                "parent": {"job_id": "b" * 32, "status": "failed"},
                "omp_children": [], "integration_authorized": False,
                "recovery_receipt": {
                    "outcome": "fallback", "reason": "SIDECAR_PROTOCOL_FAILURE",
                    "declared_paths": ["demo.py"], "changed_paths": ["demo.py"],
                    "rollback_status": "verified", "root_error": "exact sidecar error",
                    "resolved_provider": "openai-codex", "resolved_model": "gpt-5.4",
                    "route_fingerprint": "f" * 64,
                },
            }

    result = asyncio.run(dispatch_coding_execution(
        Recovered(), task="execute plan", workspace=tmp_path, project="demo",
        harness=harness, receipt_root=tmp_path / "receipts", declared_paths=["demo.py"],
        unattended=True, verified_native_recovery=True, fail_closed=False,
    ))
    assert result["outcome"] == "fallback"
    assert result["native_fallback_authorized"] is True
    assert result["rollback_status"] == "verified"
    assert result["root_error"] == "exact sidecar error"


def test_canonical_service_owns_prompt_parent_and_child_validation(tmp_path):
    child = {
        "kind": "omp_worker", "child_job_id": "a" * 32, "status": "completed",
        "record": {"patch_hash": "c" * 64, "patch_guard": {"status": "accepted"}},
    }

    class Supervisor:
        async def run(self, job_id, prompt, *, before_worker):
            assert job_id == "b" * 32
            assert "Mandatory governed coding route" in prompt
            assert before_worker == "omp-callback"
            return {
                "job_id": job_id,
                "status": "completed",
                "result_summary": '{"decision":"accepted","patch_hash":"' + "c" * 64 + '","reason":"minimal scoped change","risk_findings":[]}',
            }

    class ChildLedger(_RecordingLedger):
        def children(self, job_id):
            assert job_id == "b" * 32
            return [child]

    service = object.__new__(PrimeToolService)
    service.ledger = ChildLedger()
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, Supervisor())
    service._coding_before_worker = lambda task, _packet: "omp-callback" if task == "execute plan" else None
    result = asyncio.run(service.execute_coding_plan({
        "task": "execute plan", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet("execute plan"),
    }))
    assert result["outcome"] == "blocked"
    assert result["reason"] == "OMP_PROPOSAL_UNAVAILABLE"
    assert result["integration_authorized"] is False
    assert service.ledger.notes == [(
        "b" * 32, "execution_lane",
        {"lane": "T3_PRIME_OMP", "prime_model_turn": True, "learning_loop": False},
    )]


def test_canonical_service_blocks_completed_child_rejected_by_prime(tmp_path):
    child = {
        "kind": "omp_worker", "child_omp_job_id": "a" * 32, "status": "completed",
        "record": {"patch_hash": "c" * 64, "patch_guard": {"status": "accepted"}},
    }

    class Supervisor:
        async def run(self, job_id, prompt, *, before_worker):
            return {
                "job_id": job_id,
                "status": "completed",
                "result_summary": '{"decision":"rejected","patch_hash":"' + "c" * 64 + '","reason":"patch would truncate the target","risk_findings":[]}',
            }

    class ChildLedger(_RecordingLedger):
        def children(self, job_id):
            return [child]

    service = object.__new__(PrimeToolService)
    service.ledger = ChildLedger()
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, Supervisor())
    service._coding_before_worker = lambda _task, _packet: "omp-callback"
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(),
    }))
    assert result["outcome"] == "blocked"
    assert result["reason"] == "PRIME_REJECTED_OMP_PATCH"
    assert result["integration_authorized"] is False
    assert result["prime_review"]["decision"] == "rejected"


def test_canonical_service_preserves_safe_rejection_when_prime_mistypes_patch_hash(tmp_path):
    child = {
        "kind": "omp_worker", "child_omp_job_id": "a" * 32, "status": "completed",
        "record": {"patch_hash": "c" * 64, "patch_guard": {"status": "accepted"}},
    }

    class Supervisor:
        async def run(self, job_id, prompt, *, before_worker):
            return {
                "job_id": job_id,
                "status": "completed",
                "result_summary": '{"decision":"rejected","patch_hash":"' + "d" * 64 + '","reason":"syntax risk","risk_findings":[]}',
            }

    class ChildLedger(_RecordingLedger):
        def children(self, job_id):
            return [child]

    service = object.__new__(PrimeToolService)
    service.ledger = ChildLedger()
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, Supervisor())
    service._coding_before_worker = lambda _task, _packet: "omp-callback"
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(),
    }))
    assert result["outcome"] == "blocked"
    assert result["reason"] == "PRIME_REJECTED_OMP_PATCH"
    assert result["prime_review"]["patch_hash"] == "c" * 64
    assert "PRIME_REPORTED_PATCH_HASH_MISMATCH" in result["prime_review"]["risk_findings"]


def test_canonical_service_accepts_final_structured_review_after_brief(tmp_path):
    child = {
        "kind": "omp_worker", "child_job_id": "a" * 32, "status": "completed",
        "record": {"patch_hash": "c" * 64, "patch_guard": {"status": "accepted"}},
    }

    class Supervisor:
        async def run(self, job_id, prompt, *, before_worker):
            return {
                "job_id": job_id, "status": "completed",
                "result_summary": "Reviewed safely.\n" + '{"decision":"accepted","patch_hash":"' + "c" * 64 + '","reason":"scoped","risk_findings":[]}',
            }

    class ChildLedger(_RecordingLedger):
        def children(self, job_id):
            return [child]

    service = object.__new__(PrimeToolService)
    service.ledger = ChildLedger()
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, Supervisor())
    service._coding_before_worker = lambda _task, _packet: "omp-callback"
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(),
    }))
    assert result["outcome"] == "blocked"
    assert result["reason"] == "OMP_PROPOSAL_UNAVAILABLE"
    assert result["prime_review"]["decision"] == "accepted"


def test_canonical_service_runs_host_verifier_for_accepted_omp_patch(tmp_path, monkeypatch):
    from prime_bridge import mcp_server

    child = {
        "kind": "omp_worker", "child_job_id": "a" * 32, "status": "completed",
        "record": {"patch_hash": "c" * 64, "patch_guard": {"status": "accepted"}},
    }

    class Supervisor:
        async def run(self, job_id, prompt, *, before_worker):
            return {
                "job_id": job_id, "status": "completed",
                "result_summary": '{"decision":"accepted","patch_hash":"' + "c" * 64 + '","reason":"scoped","risk_findings":[]}',
            }

    class ChildLedger(_RecordingLedger):
        def children(self, job_id):
            return [child]

    verified = {}
    monkeypatch.setattr(mcp_server, "verify_and_apply_patch", lambda **kwargs: verified.update(kwargs) or {"status": "VERIFIED_OK"}, raising=False)
    service = object.__new__(PrimeToolService)
    service.ledger = ChildLedger()
    service.omp_service = type("Omp", (), {"test_policy": "test-policy"})()
    service._coding_omp_results = {"b" * 32: {"patch": {"patch_hash": "c" * 64}, "capability_receipt": "d" * 64}}
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, Supervisor())
    service._coding_before_worker = lambda _task, _packet: "omp-callback"
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(),
    }))
    assert result["outcome"] == "complete"
    assert result["host_apply"]["status"] == "VERIFIED_OK"
    assert verified["patch"]["patch_hash"] == "c" * 64


def test_receipt_v3_adds_redacted_telemetry(tmp_path):
    child = {
        "kind": "omp_worker", "child_job_id": "a" * 32, "status": "completed",
        "record": {
            "error": "canary-secret-value",
            "sdk_evidence": {"proposal_count": 2},
        },
    }
    service = Service({"job_id": "b" * 32, "status": "completed"}, [child])
    result = asyncio.run(dispatch_coding_execution(
        service, task="update tests", workspace=tmp_path, project="demo",
        harness="codex", receipt_root=tmp_path / "receipts", declared_paths=["demo.py"],
    ))
    assert result["schema_version"] == "3.0"
    assert result["protocol_version"] == "omp-proposal-success-phase0"
    assert result["task_class"] == "coding_with_tests"
    assert result["proposal_count"] == 2
    assert result["review_decision"] is None
    assert result["omp_errors"] == [{"sha256": result["omp_errors"][0]["sha256"], "length": 19}]
    assert "canary-secret-value" not in json.dumps(result)


def test_v2_receipt_normalizes_without_inventing_telemetry():
    legacy = {"schema_version": "2.0", "execution_id": "a" * 32, "outcome": "blocked"}
    normalized = normalize_receipt(legacy)
    assert normalized["schema_version"] == "3.0"
    assert normalized["compatibility_source_schema"] == "2.0"
    assert normalized["protocol_version"] == "legacy-v2"
    assert normalized["task_class"] == "unknown"
    assert normalized["proposal_count"] == 0


def test_native_recovery_resolves_dotnet_paths_without_machine_literals():
    source = (Path(__file__).resolve().parents[2] / "prime_bridge/native_recovery.py").read_text(encoding="utf-8")
    assert "/AIStorage/" not in source
    assert ("/" + "mnt/c/" + "Users/") not in source
    assert ("C:" + chr(92) + "Users") not in source


@pytest.mark.parametrize("packet", [None, {**_task_packet(), "packet_hash": "0" * 64}])
def test_canonical_service_rejects_missing_or_mismatched_packet_before_job(tmp_path, packet):
    service = object.__new__(PrimeToolService)
    service._prepare_job = lambda _request: pytest.fail("job must not be prepared")
    arguments = {
        "task": "update one docstring", "harness": "codex",
        "workspace": str(tmp_path), "project": "demo", "session_id": "session-1",
        "wait": True,
    }
    if packet is not None:
        arguments["task_packet"] = packet
    with pytest.raises(PrimeToolError):
        asyncio.run(service.execute_coding_plan(arguments))


def test_canonical_service_blocks_oversized_governed_context_before_job(tmp_path):
    service = object.__new__(PrimeToolService)
    service._prepare_job = lambda _request: pytest.fail("job must not be prepared")
    budget = {
        "max_context_bytes": 64, "max_output_tokens": 128, "max_wall_seconds": 30,
        "max_tool_calls": 2, "max_omp_repair_turns": 1,
        "max_prime_review_bytes": 128, "max_provider_calls": 1,
    }
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(), "task_tier": "atomic", "execution_budget": budget,
        "context_projection": {"profile": "governed", "surface": "task_packet", "sections": [{
            "category": "context", "content": "x" * 100, "required": False,
            "priority": 0, "source_type": "summary",
        }]},
    }))
    assert result["outcome"] == "blocked"
    assert result["reason"] == "BUDGET_EXHAUSTED"


def test_canonical_service_requires_provider_budget_for_prime_and_omp(tmp_path):
    service = object.__new__(PrimeToolService)
    service._prepare_job = lambda _request: pytest.fail("job must not be prepared")
    budget = {
        "max_context_bytes": 4096, "max_output_tokens": 128, "max_wall_seconds": 30,
        "max_tool_calls": 2, "max_omp_repair_turns": 0,
        "max_prime_review_bytes": 128, "max_provider_calls": 1,
    }
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(), "task_tier": "atomic", "execution_budget": budget,
    }))
    assert result["reason"] == "BUDGET_EXHAUSTED"


def test_adaptive_depth_cannot_disable_a_mandatory_gate(tmp_path):
    service = object.__new__(PrimeToolService)
    service._prepare_job = lambda _request: pytest.fail("job must not be prepared")
    budget = {
        "max_context_bytes": 4096, "max_output_tokens": 128, "max_wall_seconds": 30,
        "max_tool_calls": 2, "max_omp_repair_turns": 0,
        "max_prime_review_bytes": 128, "max_provider_calls": 2,
    }
    depth = {"max_files": 1, "max_changed_bytes": 10, "max_complexity": 1,
             "max_tool_calls": 1, "max_repair_turns": 1, "max_review_bytes": 64,
             "require_prime": True, "require_omp": False, "require_containment": True,
             "require_patch_guard": True, "require_host_apply": True, "require_host_verification": True}
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(), "task_tier": "atomic", "execution_budget": budget,
        "depth_limits": depth,
    }))
    assert result["reason"] == "BUDGET_EXHAUSTED"


def test_provider_budget_caps_omp_repairs():
    assert bounded_repair_turns({"provider_calls": 2, "repair_turns": 4}) == 0
    assert bounded_repair_turns({"provider_calls": 3, "repair_turns": 4}) == 1
    with pytest.raises(OmpServiceError, match="at least two"):
        bounded_repair_turns({"provider_calls": 1, "repair_turns": 0})


def test_prime_review_respects_runtime_byte_limit():
    child = {"record": {"patch_hash": "c" * 64, "patch_guard": {"status": "accepted"}}}
    summary = '{"decision":"accepted","patch_hash":"' + "c" * 64 + '","reason":"scoped","risk_findings":[]}'
    assert _prime_patch_review(summary, child, max_review_bytes=len(summary.encode()))["decision"] == "accepted"
    assert _prime_patch_review(summary, child, max_review_bytes=len(summary.encode()) - 1)["decision"] == "invalid"


@pytest.mark.parametrize("harness", ["hermes", "codex", "claude-code", "moa"])
def test_canonical_service_recovery_is_explicit_and_cross_harness(tmp_path, harness):
    child = {
        "kind": "omp_worker", "child_job_id": "a" * 32, "status": "failed",
        "record": {"error": "SIDECAR_PROTOCOL_FAILURE: malformed finalize response"},
    }

    class Supervisor:
        async def run(self, job_id, _prompt, *, before_worker):
            assert before_worker == "omp-callback"
            return {"job_id": job_id, "status": "failed", "result_summary": None}

    class ChildLedger(_RecordingLedger):
        def children(self, _job_id):
            return [child]

    calls = []

    async def recover(**arguments):
        calls.append(arguments)
        return {
            "outcome": "fallback", "reason": "SIDECAR_PROTOCOL_FAILURE",
            "changed_paths": ["demo.py"], "rollback_status": "verified",
            "root_error": str(arguments["omp_error"]),
        }

    service = object.__new__(PrimeToolService)
    service.ledger = ChildLedger()
    service.recovery_executor = recover
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, Supervisor())
    service._coding_before_worker = lambda _task, _packet: "omp-callback"
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": harness, "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(), "unattended": True,
        "verified_native_recovery": True, "fail_closed": False,
    }))
    assert result["outcome"] == "fallback"
    assert result["recovery_receipt"]["rollback_status"] == "verified"
    assert calls[0]["omp_error"] == "SIDECAR_PROTOCOL_FAILURE: malformed finalize response"
    assert service.ledger.notes[0][1:] == (
        "execution_lane",
        {"lane": "T4_AUTONOMOUS", "prime_model_turn": True, "learning_loop": True},
    )


def test_canonical_service_fail_closed_prevents_recovery(tmp_path):
    child = {
        "kind": "omp_worker", "child_job_id": "a" * 32, "status": "failed",
        "record": {"error": "SIDECAR_PROTOCOL_FAILURE: malformed finalize response"},
    }

    class Supervisor:
        async def run(self, job_id, _prompt, *, before_worker):
            return {"job_id": job_id, "status": "failed", "result_summary": None}

    class ChildLedger(_RecordingLedger):
        def children(self, _job_id):
            return [child]

    async def forbidden_recovery(**_arguments):
        pytest.fail("fail-closed must prevent recovery")

    service = object.__new__(PrimeToolService)
    service.ledger = ChildLedger()
    service.recovery_executor = forbidden_recovery
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, Supervisor())
    service._coding_before_worker = lambda _task, _packet: "omp-callback"
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(), "unattended": True,
        "verified_native_recovery": True, "fail_closed": True,
    }))
    assert result["outcome"] == "blocked"
    assert result["recovery_receipt"] is None


def test_canonical_service_builds_subscription_recovery_executor_by_default(tmp_path, monkeypatch):
    from prime_bridge import mcp_server

    child = {
        "kind": "omp_worker", "child_job_id": "a" * 32, "status": "failed",
        "record": {"error": "SIDECAR_PROTOCOL_FAILURE: invalid final proposal paths"},
    }

    class Supervisor:
        async def run(self, job_id, _prompt, *, before_worker):
            return {"job_id": job_id, "status": "failed", "result_summary": None}

    class ChildLedger(_RecordingLedger):
        def children(self, _job_id):
            return [child]

    observed = {}
    monkeypatch.setattr(mcp_server, "_profile_route", lambda profile, config: ({
        "provider": "openai-codex", "model": "gpt-5.4", "fingerprint": "f" * 64,
    }, {}))

    def build_executor(**arguments):
        observed.update(arguments)
        return (lambda _root, _paths: None), (lambda _root, _paths: [])

    def run_recovery(**arguments):
        observed["execution"] = arguments
        return {
            "outcome": "fallback", "reason": "SIDECAR_PROTOCOL_FAILURE",
            "changed_paths": ["demo.py"], "rollback_status": "verified",
        }

    monkeypatch.setattr(mcp_server, "make_hermes_recovery_executor", build_executor)
    monkeypatch.setattr(mcp_server, "execute_verified_recovery", run_recovery)
    service = object.__new__(PrimeToolService)
    service.profile = "gpt-5.4-codex"
    service.profile_config = tmp_path / ".hermes/profiles/gpt-5.4-codex/config.yaml"
    service.ledger = ChildLedger()
    service._prepare_job = lambda _request: ({"job_id": "b" * 32}, Supervisor())
    service._coding_before_worker = lambda _task, _packet: "omp-callback"
    result = asyncio.run(service.execute_coding_plan({
        "task": "update one docstring", "harness": "codex", "workspace": str(tmp_path),
        "project": "demo", "session_id": "session-1", "wait": True,
        "task_packet": _task_packet(), "unattended": True,
        "verified_native_recovery": True, "fail_closed": False,
    }))
    assert result["outcome"] == "fallback"
    assert observed["provider_id"] == "openai-codex"
    assert observed["model_id"] == "gpt-5.4"
    assert observed["execution"]["requested_profile"] == "gpt-5.4-codex"


def test_canonical_host_uses_packet_static_verification():
    class Policy:
        def command(self, command_id):
            raise AssertionError(f"default static verification must not resolve {command_id}")

    packet = compile_atomic_task_packet(objective="update one docstring", path="demo.py")
    assert _host_verification_command(packet, Policy()) == "packet_static"


def test_canonical_host_requires_packet_command_to_be_allowlisted():
    observed = []

    class Policy:
        def command(self, command_id):
            observed.append(command_id)
            if command_id != "pytest":
                from prime_bridge.constrained_test_runner import TestRunnerError
                raise TestRunnerError("not allowlisted")
            return object()

    packet = compile_atomic_task_packet(
        objective="repair with tests", path="demo.py",
        verification_intent=("command:pytest",),
    )
    assert _host_verification_command(packet, Policy()) == "pytest"
    assert observed == ["pytest"]

    denied = compile_atomic_task_packet(
        objective="repair", path="demo.py",
        verification_intent=("command:arbitrary-shell",),
    )
    with pytest.raises(PrimeToolError, match="not allowlisted"):
        _host_verification_command(denied, Policy())


def test_external_test_review_note_is_scoped_to_real_test_packets():
    static = compile_atomic_task_packet(objective="repair", path="demo.py")
    external = compile_atomic_task_packet(
        objective="repair and test", path="demo.py",
        verification_intent=("command:pytest",),
    )
    assert _external_test_review_note(static) == ""
    note = _external_test_review_note(external)
    assert "may not see the host workspace by design" in note
    assert "Do not reject solely" in note
    assert "run the bound command" in note
