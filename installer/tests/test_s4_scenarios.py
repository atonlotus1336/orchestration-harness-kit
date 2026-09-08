"""Scenario boundary checks: no provider, scheduler, or runtime mutation."""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "prime_bridge").is_dir():
    sys.path.insert(0, str(ROOT))
    PACKAGE = "prime_bridge.adaptive_review"
else:
    sys.path.insert(0, str(ROOT / "modules/orchestration-core"))
    PACKAGE = "lapart_core.review"
recommend_s4 = importlib.import_module(PACKAGE + ".s4_scenarios").recommend_s4
load_policy = importlib.import_module(PACKAGE + ".policy").load_policy


@pytest.fixture
def facts():
    return {
        "endpoint_fixed": True, "goal_open": True, "safe_boundary": True,
        "source_verified": True, "system_gate_required": True, "active_work_due": False,
        "cross_system_weakness": False, "loop_limit_reached": False,
        "same_source_pending": False, "budget_known": True, "event": "milestone",
        "goal_id": "example-goal", "contract_hash": "a"*64, "source_identity": "b"*64,
        "previous_s4_source": None, "explicit_action": "none",
        "changed_paths": ["one/api.py", "two/schema.py", "three/service.py"],
        "structural_paths": [], "subsystem_count": 3,
        "remaining_budget": dict(load_policy().data["budgets"]["S4"]),
    }


def check(facts):
    value = recommend_s4(facts, load_policy().data)
    assert value["automatic_start_authorized"] is False
    assert value["model_calls"] == 0
    return value


@pytest.mark.parametrize("event,extra,scenario", [
    ("milestone", {}, "milestone_system_gate"),
    ("pre_completion", {}, "precompletion_system_gate"),
    ("checkpoint", {"structural_paths": ["one/api.py", "two/schema.py"]}, "material_architecture_change"),
    ("active_work_goalpost", {"active_work_due": True, "cross_system_weakness": True}, "goalpost_cross_system_weakness"),
])
def test_defined_scenarios_are_candidates_only(facts, event, extra, scenario):
    facts.update(event=event, **extra)
    result = check(facts)
    assert result["status"] == "CANDIDATE"
    assert scenario in result["scenarios"]


@pytest.mark.parametrize("field,value,status", [
    ("explicit_action", "Later", "DEFERRED"), ("explicit_action", "Skip", "DEFERRED"),
    ("goal_open", False, "NOT_NEEDED"), ("loop_limit_reached", True, "CHECKPOINT"),
    ("safe_boundary", False, "DEFERRED"), ("source_verified", False, "DEFERRED"),
    ("endpoint_fixed", False, "DEFERRED"), ("budget_known", False, "DEFERRED"),
    ("same_source_pending", True, "NOT_NEEDED"), ("previous_s4_source", "b"*64, "NOT_NEEDED"),
    ("source_verified", "true", "DEFERRED"), ("contract_hash", "", "DEFERRED"),
    ("subsystem_count", True, "DEFERRED"), ("event", "timer", "DEFERRED"),
    ("structural_paths", ["outside.py"], "DEFERRED"),
])
def test_denials_override_candidate(facts, field, value, status):
    facts[field] = value
    assert check(facts)["status"] == status


def test_timer_alone_is_insufficient(facts):
    facts.update(event="active_work_goalpost", active_work_due=True)
    assert check(facts)["status"] == "NOT_NEEDED"


def test_small_architecture_change_stays_below_s4(facts):
    facts.update(event="checkpoint", subsystem_count=2,
                 structural_paths=["one/api.py", "two/schema.py"])
    assert check(facts)["status"] == "NOT_NEEDED"


@pytest.mark.parametrize("budget_key", sorted(load_policy().data["budgets"]["S4"]))
def test_any_insufficient_budget_defers_without_enlargement(facts, budget_key):
    facts["remaining_budget"][budget_key] -= 1
    assert check(facts)["status"] == "DEFERRED"


def test_native_hook_deadline_does_not_fit_system_review(facts):
    facts["remaining_budget"]["max_elapsed_seconds"] = 240
    assert check(facts)["status"] == "DEFERRED"


def test_missing_unknown_and_malformed_fields_defer(facts):
    for field in ("source_verified", "remaining_budget", "contract_hash"):
        partial = dict(facts)
        partial.pop(field)
        assert check(partial)["status"] == "DEFERRED"
    assert check(dict(facts, arbitrary_model_claim=True))["status"] == "DEFERRED"


def test_deduplication_survives_event_change(facts):
    first = check(facts)["deduplication_key"]
    facts["event"] = "pre_completion"
    assert check(facts)["deduplication_key"] == first
    facts["source_identity"] = "c"*64
    assert check(facts)["deduplication_key"] != first

@pytest.mark.parametrize("field,value", [
    ("event", []), ("explicit_action", {}), ("remaining_budget", None),
    ("changed_paths", ["../outside.py"]), ("changed_paths", ["/outside.py"]),
    ("changed_paths", ["a.py", "a.py"]), ("changed_paths", ["./a.py"]),
    ("structural_paths", ["one/api.py", "one/api.py"]),
])
def test_malformed_input_defers_instead_of_raising(facts, field, value):
    facts[field] = value
    assert check(facts)["status"] == "DEFERRED"


def test_malformed_policy_defers(facts):
    assert recommend_s4(facts, {"budgets": None})["status"] == "DEFERRED"


def test_file_ceiling_defers(facts):
    facts["changed_paths"] = ["file" + str(i) + ".py" for i in range(81)]
    assert check(facts)["status"] == "DEFERRED"
