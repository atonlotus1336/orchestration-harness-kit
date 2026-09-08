from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "modules/orchestration-core"))
from test_orchestration_core import make_core, request
from lapart_core.errors import CoreError
from lapart_core.review.interface import classify_review
from lapart_core.review.catalog import Catalog
from lapart_core.review.models import Pipeline, Verdict
from lapart_core.review.runner import Runner


def route(task, tier="atomic", review=None):
    payload = {"task": task, "task_tier": tier}
    if review is not None:
        payload["review"] = review
    return make_core().handle(request("routing.prime.select", payload))


def bounded(**extra):
    return dict(text="Implement a small change", declared_paths=["src/a.py"],
                risk_tier="low", semantic_assertions=["The declared behavior passes"], **extra)


@pytest.mark.parametrize("task,tier", [
    ({"multi_system": True}, "atomic"), ({}, "high_risk"),
    ({"autonomous": False}, "complex"), ({"requires_omp": True}, "atomic"),
])
def test_risk_or_missing_scope_does_not_authorize_autonomy(task, tier):
    result = route(task, tier)["payload"]
    assert result["actual_lane"] == "T3"
    assert result["execution_started"] is False


@pytest.mark.parametrize("text", [
    "Explain how to run autonomously",
    'Document the example "run autonomously"',
    "Do not run autonomously",
    "Never execute unattended",
])
def test_discussion_quotes_and_negation_do_not_activate_t4(text):
    task = bounded(); task["text"] = text
    assert route(task)["payload"]["actual_lane"] != "T4"


@pytest.mark.parametrize("task", [{"autonomous": True}, {"text": "Run this autonomously"}])
def test_explicit_autonomous_intent_selects_t4(task):
    assert route(task)["payload"]["actual_lane"] == "T4"


@pytest.mark.parametrize("task,lane", [
    (dict(text="Update typo", declared_paths=["a.txt"], risk_tier="low", semantic_assertions=["Spelling is correct"]), "T0"),
    (bounded(), "T1"),
    (dict(text="Implement bounded work", declared_paths=["a.py", "b.py"], risk_tier="low", verification_command_id="pytest"), "T2"),
])
def test_lower_lanes_require_bounded_facts_and_explain_policy(task, lane):
    result = route(task)["payload"]
    assert result["recommended_lane"] == lane
    assert result["actual_lane"] == ("T3" if lane == "T2" else lane)
    assert result["policy_override"] is (lane == "T2")
    assert result["parallel_safe"] is (lane != "T2")
    assert result["actual_lane_reason"]


@pytest.mark.parametrize("task", [None, "text", [], {"autonomous": "true"},
    {"risk_tier": []}, {"risk_tier": "unknown"}, {"declared_paths": "../escape"},
    {"declared_paths": ["../escape"]}, {"semantic_assertions": [""]}])
def test_invalid_execution_facts_fail_closed(task):
    assert route(task)["error"]["code"] == "ROUTING_FACTS_INVALID"


@pytest.mark.parametrize("count,level", [(2,"S0"), (3,"S1"), (7,"S2"), (16,"S3")])
def test_review_scope_cannot_be_downgraded(count, level):
    result = classify_review({"routing_facts":{key:True for key in ("mechanical_or_exact","independent_deterministic_validation","no_unknown_load_bearing_fact","no_risk_trigger")},"changed_lines":1,"subsystem_count":1},
        declared_paths=[f"src/file{n}.py" for n in range(count)])
    assert result["pipeline"] == level
    assert result["review_completed"] is False
    assert result["reviewer_invoked"] is False


@pytest.mark.parametrize("path,level", [("tests/test_a.py","S2"), ("src/auth.py","S3"),
    ("src/routing.py","S3"), ("docs/auth.md","S1")])
def test_review_uses_semantic_risk_floors(path, level):
    assert classify_review({}, declared_paths=[path])["pipeline"] == level


def test_multiple_subsystems_requires_s3_and_unknowns_do_not_overpromote():
    result = classify_review({"subsystem_count":2}, declared_paths=["src/a.py"])
    assert result["pipeline"] == "S3"
    assert result["routing_verdict"] == "PASS"


def test_scope_ceiling_is_inconclusive_and_s4_needs_confirmation():
    result = classify_review({}, declared_paths=[f"file{n}.py" for n in range(41)])
    assert result["routing_verdict"] == "INCONCLUSIVE"
    assert result["runnable"] is False
    result = classify_review({"scope":"system"}, declared_paths=["a.py"])
    assert result["requires_confirmation"] is True
    assert result["runnable"] is False


def test_execution_and_review_are_independent_and_provider_free():
    result = route(bounded(), review={"required_level":"S3"})
    assert result["payload"]["actual_lane"] == "T1"
    assert result["payload"]["review"]["pipeline"] == "S3"
    assert result["provider_accounting"]["provider_calls"] == 0
    assert result["payload"]["review"]["review_completed"] is False


@pytest.mark.parametrize("review", [{"actual_lane":"T0"}, {"changed_lines":True},
    {"subsystem_count":-1}, {"s4_confirmed":"yes"}, {"facts":"bad"}])
def test_review_rejects_malformed_or_execution_facts(review):
    with pytest.raises(CoreError):
        classify_review(review, declared_paths=["a.py"])


@pytest.mark.parametrize("pipeline", list(Pipeline))
def test_runner_never_claims_empty_catalog_completed(pipeline):
    result = Runner(Catalog(), {}, ()).run(
        pipeline=pipeline, triggers=(), contents={"a.py":"pass"},
        source_hashes={}, hash_provider=lambda paths: {}, providers={}, s4_confirmed=True,
    )
    assert result.verdict is Verdict.INCONCLUSIVE
    assert not result.invoked_reviewers


def test_goalpost_runtime_is_part_of_installed_core():
    import json
    manifest = json.loads((ROOT/"installer/modules/module-manifest.json").read_text())
    entry = next(x for x in manifest["modules"] if x["id"]=="orchestration-core")
    assert (ROOT/entry["source"]/"goalposts.py").is_file()
    assert (ROOT/entry["source"]/"lapart_core/goalposts.py").is_file()

import hashlib
from lapart_core.review.catalog import Reviewer
from lapart_core.review.adapters import ReviewerCapability, ReportOnlyProvider

def runner_case(tmp_path, ids, *, pipeline, triggers=("change",)):
    skill = tmp_path / "skill.md"
    skill.write_text("review only")
    digest = hashlib.sha256(skill.read_bytes()).hexdigest()
    catalog = Catalog()
    calls = []
    for identifier in ids:
        catalog.register(Reviewer(identifier, "test", ("change",), str(skill), digest), ReviewerCapability(("a.py",)))
    runner = Runner(catalog, {}, ())
    result = runner.run(pipeline=pipeline, triggers=triggers, contents={"a.py":"ok"}, source_hashes={"a.py":"hash"}, hash_provider=lambda paths: {"a.py":"hash"}, providers={identifier: ReportOnlyProvider(lambda capability: calls.append(True)) for identifier in ids})
    return result, calls


def test_s0_never_invokes_model_even_with_triggered_provider(tmp_path):
    result, calls = runner_case(tmp_path, ["ponytail-review"], pipeline=Pipeline.S0)
    assert not calls and result.verdict is Verdict.INCONCLUSIVE
    assert "S0" in result.controls[0]


def test_s1_cannot_launch_architecture_reviewer(tmp_path):
    result, calls = runner_case(tmp_path, ["improve-codebase-architecture"], pipeline=Pipeline.S1)
    assert not calls and "outside the selected pipeline" in result.controls[0]


def test_empty_catalog_cannot_report_completed_review(tmp_path):
    result, calls = runner_case(tmp_path, [], pipeline=Pipeline.S1)
    assert not calls and result.verdict is Verdict.INCONCLUSIVE


def test_required_reviewers_refuse_unmetered_provider_even_without_trigger(tmp_path):
    result, calls = runner_case(tmp_path, ["ponytail-review"], pipeline=Pipeline.S1, triggers=())
    assert calls == [] and result.verdict is Verdict.INCONCLUSIVE


def test_model_call_budget_is_checked_before_any_provider(tmp_path):
    result, calls = runner_case(tmp_path, ["ponytail-review", "evidence-integrity-audit", "specialist-a", "specialist-b"], pipeline=Pipeline.S2)
    assert not calls and "model-call budget" in result.controls[0]


def test_t2_missing_verification_keeps_prime():
    result = route(dict(text="Implement bounded work", declared_paths=["a.py", "b.py"], risk_tier="low"))["payload"]
    assert result["recommended_lane"] == "T3"
    assert result["actual_lane"] == "T3"

def test_shared_contract_can_select_t2_without_changing_other_lanes():
    import json
    from dataclasses import replace
    from lapart_core.policy import CorePolicy
    from lapart_core.routing import select_prime_lane
    policy = CorePolicy.load(ROOT / "modules/orchestration-core/core-policy.json")
    value = json.loads(json.dumps(dict(policy.execution_value)))
    value["promoted_lanes"].append("T2_OMP_LITE")
    candidate = replace(policy, t2_actual_lane="T2", execution_value=value)
    task = dict(text="Implement bounded work", declared_paths=["a.py", "b.py"], risk_tier="low",
                verification_command_id="pytest")
    assert select_prime_lane(task, "atomic", candidate)["actual_lane"] == "T2"
    assert select_prime_lane({**task, "risk_tier": "high"}, "atomic", candidate)["actual_lane"] == "T3"
    assert select_prime_lane({**task, "autonomous": True}, "atomic", candidate)["actual_lane"] == "T4"
    assert select_prime_lane(bounded(), "atomic", candidate)["actual_lane"] == "T1"


def test_irrelevant_fact_cannot_remove_missing_s0_evidence():
    before = classify_review({}, declared_paths=["example.py"])
    after = classify_review({"facts":[{"key":"irrelevant","required_level":"S0","known":True,"load_bearing":False}]}, declared_paths=["example.py"])
    assert before["pipeline"] == after["pipeline"] == "S1"
    assert after["review_completed"] is False
