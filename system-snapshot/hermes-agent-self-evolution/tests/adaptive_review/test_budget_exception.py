"""Synthetic approvals and local HTTP only; never authorizes installed review."""
import copy
import json
from pathlib import Path
import time

import pytest
from test_controller import server, setup_controller
from prime_bridge.adaptive_review import budget_exception as be
from prime_bridge.adaptive_review.controller import atomic, digest


def prepare(tmp_path, server, *, enabled=True, spent=None):
    owner, args = setup_controller(tmp_path, server, pipeline="S3")
    owner.config["budget_exceptions_enabled"] = enabled
    args["evidence"]["binding"]["lifecycle"] = {"owner_id": "a" * 32}
    # Large enough to exceed the default aggregate S3 budget, within its file cap.
    (args["workspace"] / "example.py").write_text("# evidence\n" * 4000)
    # This synthetic receipt describes the large fixture baseline.
    import hashlib
    args["evidence"]["before"]["contents"]["example.py"] = hashlib.sha256(
        (args["workspace"] / "example.py").read_bytes()).hexdigest()
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
    from prime_bridge.adaptive_review.budget_approval import ApprovalBridge
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
    from prime_bridge.adaptive_review.runner import Runner
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
    from prime_bridge.adaptive_review import runner
    owner, args, proposal = prepare(tmp_path, server)
    approve_fixture(owner, proposal)
    prompt = runner.review_prompt
    monkeypatch.setattr(runner, "review_prompt", lambda *a, **kw: prompt(*a, **kw) + " changed")
    result = owner.run(**args, budget_exception_id=proposal["id"])
    assert result["review_verdict"] == "INCONCLUSIVE"
    assert "approved exception packet changed before dispatch" in result["controls"]
    assert not server["requests"]


def test_exception_prepares_the_same_active_career_packets_as_dispatch(
    tmp_path, server, monkeypatch,
):
    owner, args = setup_controller(tmp_path, server, pipeline="S3")
    root = Path(__file__).resolve().parents[2]
    owner.catalog_path = root / "config/adaptive-reviewer-catalog.json"
    owner.vault = root.parent
    owner.config["budget_exceptions_enabled"] = True
    args["evidence"]["binding"]["lifecycle"] = {"owner_id": "a" * 32}
    from prime_bridge.adaptive_review.careers import CareerQualification

    reviewers = (
        ("ponytail-review", "engineer", "behavioral_correctness"),
        ("evidence-integrity-audit", "physicist", "evidence_integrity"),
        ("improve-codebase-architecture", "structuralist", "architecture_ownership"),
    )
    qualifications = tuple(
        CareerQualification(
            reviewer_id=reviewer,
            career_id=career,
            covered_obligations=(obligation,),
            state="production_authorized",
        )
        for reviewer, career, obligation in reviewers
    )
    controls = {
        "career_enabled": True,
        "temperament_enabled": True,
        "career_shadow_pass": True,
        "purpose": "production",
        "candidate_canary_authorized": False,
        "now_epoch": 1,
        "model_route": "default",
        "workload_class": "default",
        "layer_configuration": "career",
    }
    parallel = dict(be.load_policy().data["parallel_review"])
    parallel["activation_state"] = "production"
    monkeypatch.setattr(
        owner,
        "_adaptive_review_inputs",
        lambda pipeline, *, lineage, harness: (
            parallel, qualifications, controls, "a" * 64,
        ),
    )
    limits = dict(be.load_policy().data["budgets"]["S3"])
    limits.update(max_estimated_input_tokens=300000, max_total_tokens=310240)
    proposal = owner.prepare_budget_exception(
        **args, limits=limits, expires_at=int(time.time()) + 600,
    )
    approve_fixture(owner, proposal)

    result = owner.run(**args, budget_exception_id=proposal["id"])

    assert result["review_verdict"] == "PASS", result
    assert result["model_calls"] == 3


def test_exception_prepares_packets_with_the_selected_reviewer_transport(
    tmp_path, server, monkeypatch,
):
    owner, args = setup_controller(tmp_path, server, pipeline="S1")
    owner.config["budget_exceptions_enabled"] = True
    args["evidence"]["binding"]["lifecycle"] = {"owner_id": "a" * 32}
    calls = []

    class DistinctTransport:
        def payload(self, skill, contents, output_cap):
            return json.dumps({
                "transport": "herdr-fixture",
                "skill": skill,
                "content": contents,
                "max_tokens": output_cap,
            }, sort_keys=True, separators=(",", ":")).encode()

    def selected(catalog, *, workspace, evidence, pipeline, delivery_number):
        calls.append((str(workspace), evidence["run_id"], pipeline, delivery_number))
        return {reviewer_id: DistinctTransport() for reviewer_id in catalog.reviewers}

    monkeypatch.setattr(owner, "_reviewer_transports", selected)
    limits = dict(be.load_policy().data["budgets"]["S1"])
    limits["max_total_tokens"] = 20480

    proposal = owner.prepare_budget_exception(
        **args, limits=limits, expires_at=int(time.time()) + 600,
    )

    assert calls == [(str(args["workspace"]), "one", "S1", 1)]
    assert proposal["packet_plan"]["request_count"] == 1
    assert proposal["packet_plan"]["packet_sha256"]["ponytail-review"]
