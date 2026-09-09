"""Synthetic approvals and local HTTP only; never authorizes installed review."""
import copy
import json
from pathlib import Path
import time

import pytest
from test_review_controller import server, setup_controller
from lapart_core.review import budget_exception as be
from lapart_core.review.controller import atomic, digest


def test_core_manifest_requires_every_runtime_module(tmp_path, monkeypatch):
    import importlib.util
    import hashlib
    kit = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('core_hash_gate', kit/'modules/orchestration-core/verify-core.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    module = tmp_path/'modules/orchestration-core'
    validator = module/'lapart_core/review/budget_approval.py'
    validator.parent.mkdir(parents=True)
    validator.write_text('validator = True\n', encoding='utf-8')
    manifest = module/'source-hashes.json'
    payload = {'schema': 'lapart.core-source-hashes.v1', 'algorithm': 'sha256', 'files': {}}
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    monkeypatch.setattr(gate, 'KIT_ROOT', tmp_path)
    monkeypatch.setattr(gate, 'MODULE_ROOT', module)
    assert any('budget_approval.py' in failure for failure in gate.verify_source_hashes())
    payload['files'][validator.relative_to(tmp_path).as_posix()] = hashlib.sha256(validator.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    assert gate.verify_source_hashes() == []
    validator.write_text('validator = False\n', encoding='utf-8')
    assert any('budget_approval.py' in failure for failure in gate.verify_source_hashes())


def prepare(tmp_path, server, *, enabled=True, spent=None):
    owner, args = setup_controller(tmp_path, server, pipeline="S3")
    owner.config["budget_exceptions_enabled"] = enabled
    args["evidence"]["binding"]["lifecycle"] = {"owner_id": "a" * 32}
    # Large enough to exceed the default aggregate S3 budget, within its file cap.
    (args["workspace"] / "example.py").write_text("# evidence\n" * 4000)
    current = __import__("hashlib").sha256((args["workspace"] / "example.py").read_bytes()).hexdigest()
    args["evidence"]["before"] = {"contents": {"example.py": current}, "index": {}}
    args["evidence"]["after"]["contents"] = {"example.py": current}
    limits = dict(be.load_policy().data["budgets"]["S3"])
    limits.update(max_estimated_input_tokens=300000, max_total_tokens=310240)
    if spent is not None:
        ev = args["evidence"]
        lineage = digest({"workspace": str(args["workspace"]), "harness": ev["harness"],
            "session": ev["binding"]["session_binding_sha256"], "task": ev["packet"]["objective"]})
        atomic(owner.state / "lineages" / (lineage + ".json"), owner._signed({
            "deliveries": 1, "limits": be.load_policy().data["budgets"]["S3"], "spent": spent}))
    proposal = owner.prepare_budget_exception(**args, limits=limits, expires_at=int(time.time()) + 600)
    return owner, args, proposal


def approve_fixture(owner, proposal):
    # Synthetic ledger interface. Real broker/SQLite ownership is tested separately.
    import asyncio, hashlib
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from lapart_core.review.budget_approval import ApprovalBridge
    record = {"request_id": "fixture", "job_id": "a" * 32, "profile": "interactive",
        "alias": "review.budget.exception", "classification": "action", "status": "executing",
        "target": {"server": "review-budget", "tool": "approve_exception"},
        "argument_hash": hashlib.sha256(json.dumps({"proposal_id": proposal["id"]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "expires_at": datetime.fromtimestamp(proposal["expires_at"], timezone.utc).isoformat(),
        "approval_provenance": {"kind": "operator", "approver": "fixture", "decided_at": datetime.now(timezone.utc).isoformat()}}
    owner.approval_ledger = SimpleNamespace(get=lambda _: copy.deepcopy(record), record=record)
    asyncio.run(ApprovalBridge(owner).call("approve_exception", {"proposal_id": proposal["id"]}, record))
    record["status"] = "executed"


def test_draft_is_inactive_and_makes_no_request(tmp_path, server):
    owner, args, proposal = prepare(tmp_path, server, enabled=False)
    approve_fixture(owner, proposal)
    result = owner.run(**args, budget_exception_id=proposal["id"])
    assert result["review_verdict"] == "INCONCLUSIVE"
    assert "disabled" in result["reason"]
    assert not server["requests"]
    assert not list(owner.state.glob("budget-exceptions/*.claim"))


def test_approved_exact_batch_once_and_cached_without_replay(tmp_path, server):
    owner, args, proposal = prepare(tmp_path, server)
    approve_fixture(owner, proposal)
    first = owner.run(**args, budget_exception_id=proposal["id"])
    assert first["review_verdict"] == "PASS", first
    assert first["model_calls"] == 3
    second = owner.run(**args, budget_exception_id=proposal["id"])
    assert second["cache_hit"] and second["model_calls"] == 0
    assert len(server["requests"]) == 3
    assert (owner.state / "budget-exceptions" / (proposal["id"] + ".claim")).exists()


@pytest.mark.parametrize("fault", ["missing_approval", "unsigned", "wrong_approval", "source", "config", "expired", "spent", "unknown", "claimed", "pipeline", "skill", "run_id"])
def test_mismatch_refuses_before_request(tmp_path, server, fault):
    owner, args, proposal = prepare(tmp_path, server)
    approve_fixture(owner, proposal)
    approval_path = owner.state / "approvals" / (proposal["id"] + ".json")
    if fault == "missing_approval":
        approval_path.unlink()
    elif fault == "unsigned":
        approval_path.write_text('{}')
    elif fault == "wrong_approval":
        record = owner._read(approval_path); record["actor_kind"] = "model"
        atomic(approval_path, owner._signed(record))
    elif fault == "source":
        (args["workspace"] / "example.py").write_text("changed")
    elif fault == "config":
        owner.config["provider"]["model"] = "changed"
    elif fault == "expired":
        record = owner._read(approval_path); record["expires_at"] = 1
        atomic(approval_path, owner._signed(record))
    elif fault in {"spent", "unknown"}:
        ledger = {"deliveries": 0, "limits": be.load_policy().data["budgets"]["S3"],
                  "spent": {"model_requests": 1}, "unknown_usage": fault == "unknown"}
        atomic(owner.state / "lineages" / (proposal["binding"]["lineage"] + ".json"), owner._signed(ledger))
    elif fault == "claimed":
        (owner.state / "budget-exceptions" / (proposal["id"] + ".claim")).touch()
    elif fault == "pipeline":
        args["pipeline"] = "S2"
    elif fault == "skill":
        (tmp_path / "skill.md").write_text("changed")
    elif fault == "run_id":
        args["evidence"]["run_id"] = "another-delivery"
    result = owner.run(**args, budget_exception_id=proposal["id"])
    assert result["review_verdict"] == "INCONCLUSIVE", result
    assert not server["requests"]


def test_exception_cannot_raise_call_output_or_time_limits(tmp_path, server):
    owner, args = setup_controller(tmp_path, server, pipeline="S3")
    for key in ("max_model_calls", "max_elapsed_seconds", "max_output_tokens", "max_files"):
        limits = dict(be.load_policy().data["budgets"]["S3"]); limits[key] += 1
        with pytest.raises(ValueError):
            owner.prepare_budget_exception(**args, limits=limits, expires_at=int(time.time()) + 600)


def test_grant_cannot_be_a_caller_supplied_mapping():
    with pytest.raises(ValueError):
        be.take_grant({"limits": {}}, "S3", {})


def test_prior_spending_and_delivery_count_are_preserved(tmp_path, server):
    owner, args, proposal = prepare(tmp_path, server, spent={"estimated_input_units": 1000, "model_requests": 1})
    assert proposal["remaining"]["max_estimated_input_tokens"] == 299000
    assert proposal["remaining"]["max_model_calls"] == 4
    approve_fixture(owner, proposal)
    result = owner.run(**args, budget_exception_id=proposal["id"])
    assert result["review_verdict"] == "PASS", result
    ledger = owner._read(owner.state / "lineages" / (proposal["binding"]["lineage"] + ".json"))
    assert ledger["deliveries"] == 2 and ledger["spent"]["model_requests"] == 4
    assert ledger["spent"]["estimated_input_units"] >= 1000 + result["budget"]["estimated_input_units"]
    assert ledger["limits"]["max_estimated_input_tokens"] == 32768


def test_concurrent_delivery_cannot_duplicate_batch(tmp_path, server):
    from concurrent.futures import ThreadPoolExecutor
    owner, args, proposal = prepare(tmp_path, server)
    approve_fixture(owner, proposal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: owner.run(**args, budget_exception_id=proposal["id"]), range(2)))
    assert any(result["review_verdict"] == "PASS" for result in results)
    assert len(server["requests"]) == 3


def test_interrupted_claim_keeps_unknown_usage_and_blocks_retry(tmp_path, server, monkeypatch):
    from lapart_core.review.runner import Runner
    owner, args, proposal = prepare(tmp_path, server)
    approve_fixture(owner, proposal)
    def interrupted(*args, **kwargs):
        raise RuntimeError("synthetic interruption")
    monkeypatch.setattr(Runner, "run", interrupted)
    first = owner.run(**args, budget_exception_id=proposal["id"])
    second = owner.run(**args, budget_exception_id=proposal["id"])
    assert first["review_verdict"] == second["review_verdict"] == "INCONCLUSIVE"
    assert "interrupted" in second["reason"]
    ledger = owner._read(owner.state / "lineages" / (proposal["binding"]["lineage"] + ".json"))
    assert ledger["unknown_usage"] is True and ledger["deliveries"] == 1
    assert not server["requests"]


def test_runner_rechecks_exact_packets_after_owner_claim(tmp_path, server, monkeypatch):
    from lapart_core.review import runner
    owner, args, proposal = prepare(tmp_path, server)
    approve_fixture(owner, proposal)
    prompt = runner.review_prompt
    monkeypatch.setattr(runner, "review_prompt", lambda *a, **kw: prompt(*a, **kw) + " changed")
    result = owner.run(**args, budget_exception_id=proposal["id"])
    assert result["review_verdict"] == "INCONCLUSIVE"
    assert "approved exception packet changed before dispatch" in result["controls"]
    assert not server["requests"]


def test_bundled_broker_projects_existing_operator_decision(tmp_path, server):
    import asyncio, sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'system-snapshot/hermes-agent-self-evolution'))
    from prime_bridge.capability_broker import CapabilityBroker
    from prime_bridge.capability_policy import CapabilityPolicy
    from prime_bridge.capability_ledger import CapabilityLedger
    from lapart_core.review.budget_approval import ApprovalBridge, ExistingApprovalReader, validate
    owner, args, proposal = prepare(tmp_path, server)
    path = tmp_path / "capability-policy.json"
    path.write_text(json.dumps({"schema_version": "1.0", "enabled": True, "approval_ttl_seconds": 900,
        "max_argument_bytes": 4096, "max_result_bytes": 4096, "aliases": {"review.budget.exception": {
        "classification": "action", "approval": "required", "server": "review-budget", "tool": "approve_exception"}}}))
    policy = CapabilityPolicy.load(path)
    ledger = CapabilityLedger(tmp_path / "capabilities.db")
    owner.approval_ledger = ExistingApprovalReader(ledger.path)
    broker = CapabilityBroker(policy, ledger, None, internal_handlers={"review-budget": ApprovalBridge(owner).call})
    request = {"job_id": "a" * 32, "profile": "interactive", "alias": "review.budget.exception",
               "arguments": {"proposal_id": proposal["id"]}}
    pending = asyncio.run(broker.handle(request))
    assert pending["status"] == "approval_required" and not server["requests"]
    assert not (owner.state / "approvals").exists()
    record = ledger.get(pending["request_id"])
    ledger.resolve(record["request_id"], profile="interactive", decision="approved", approver="fixture-human",
                   operator_nonce=record["operator_nonce"])
    projected = asyncio.run(broker.handle(dict(request, request_id=record["request_id"])))
    assert projected["status"] == "ok"
    assert ledger.get(record["request_id"])["status"] == "executed"
    validate(owner, proposal["id"], proposal)
    assert not server["requests"]
