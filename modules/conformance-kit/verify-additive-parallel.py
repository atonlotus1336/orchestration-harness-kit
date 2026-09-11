#!/usr/bin/env python3
"""Provider-free feature conformance separate from the closed protocol suite."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "modules/additive-parallel"
SCENARIOS = Path(__file__).resolve().with_name("additive-parallel-scenarios.json")


def load_feature():
    spec = importlib.util.spec_from_file_location("additive_parallel", MODULE / "verify-additive-parallel.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_switch():
    spec = importlib.util.spec_from_file_location("adaptive_switch", MODULE / "adaptive_switch.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def verify() -> list[str]:
    feature = load_feature()
    switch_module = load_switch()
    failures = feature.verify()
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if scenarios.get("schema") != "lapart.additive-parallel-scenarios.v1" or not isinstance(scenarios.get("cases"), list):
        return failures + ["scenario suite is invalid"]
    profile = json.loads((MODULE / "additive-parallel.profile.json").read_text(encoding="utf-8"))
    profile["feature_state"] = profile["core_activation_state"] = "shadow"
    for case in scenarios["cases"]:
        result = feature.admit_batch(copy.deepcopy(profile), case.get("children", []))
        if result.get("decision") != case.get("expected") or result.get("provider_calls") != 0:
            failures.append(f"scenario failed: {case.get('name', 'unnamed')}")
    adaptive_spec = importlib.util.spec_from_file_location("adaptive_v2", MODULE / "adaptive_v2.py")
    adaptive = importlib.util.module_from_spec(adaptive_spec)
    assert adaptive_spec and adaptive_spec.loader
    adaptive_spec.loader.exec_module(adaptive)
    policy = json.loads((MODULE / "adaptive-v2.policy.json").read_text(encoding="utf-8"))
    review = json.loads((MODULE / "adaptive-v2.review.json").read_text(encoding="utf-8"))
    now = __import__("datetime").datetime(2029, 1, 1, tzinfo=__import__("datetime").timezone.utc)
    for case in scenarios.get("adaptive_v2", {}).get("execution", []):
        candidate = copy.deepcopy(policy)
        candidate["activation_state"] = case["activation_state"]
        candidate["strategy"] = case["strategy"]
        if case["activation_state"] != "inactive":
            candidate["execution"].update(enabled=True, qualification_state=case.get("grant_state", "production_qualified"), qualified_ceiling=8)
            candidate["grants"]["execution_parallelism"].update(
                enabled=True, state=case.get("grant_state", "production_qualified"),
                qualification_reference="a" * 64, expiry="2030-01-01T00:00:00Z",
            )
        result = adaptive.decide_admission(candidate, case["facts"], layer="execution", now=now)
        expected_reason = case.get("expected_reason")
        if (
            result["desired_count"] != case["expected_desired"]
            or result["admitted_count"] != case["expected_admitted"]
            or expected_reason is not None and expected_reason not in result["limiting_reasons"]
        ):
            failures.append(f"adaptive execution scenario failed: {case['name']}")

    def packet(node, overrides):
        root = os.path.realpath(ROOT)
        value = {
            "schema_version": "2.0", "packet_id": f"packet-{node}", "node_id": node,
            "workspace_root": root, "read_set": [f"src/{node}.in"],
            "write_set": [f"src/{node}.py"], "resource_set": [f"resource:{node}"],
            "resource_state": "complete", "dependencies": [], "base_revision": "b" * 64,
            "expected_artifacts": [f"out/{node}.json"], "assertions": ["tests pass"],
            "budget": {"provider_calls": 0, "tokens": 0, "wall_seconds": 30, "cost": 0},
            "route_reference": "route-1", "goal_reference": "goal-1",
        }
        value.update(overrides)
        if value["workspace_root"] == "nested":
            value["workspace_root"] = os.path.realpath(ROOT / "modules")
        return value

    for case in scenarios.get("adaptive_v2", {}).get("conflicts", []):
        actual = adaptive.packets_conflict(packet("a", case["left"]), packet("b", case["right"]))
        if actual is not case["expected"]:
            failures.append(f"adaptive conflict scenario failed: {case['name']}")
    for case in scenarios.get("adaptive_v2", {}).get("review", []):
        result = adaptive.plan_review_stage(review, pipeline=case["pipeline"], selected_members=tuple(case["members"]), max_model_calls=review["max_model_calls"][case["pipeline"]])
        if result["concurrency"] != case["expected_concurrency"]:
            failures.append(f"adaptive review scenario failed: {case['name']}")
    observation = feature.observe_events("job", [{"event_id": "ok", "job_id": "job", "status": "running"}, {"event_id": "late", "job_id": "job", "status": "cancelled"}, {"event_id": "wrong", "job_id": "other", "status": "done"}])
    if observation["canonical_completion"] is not False or observation["rejected"] != ["wrong"]:
        failures.append("observation-only completion boundary failed")

    class Adapter:
        def __init__(self):
            self.active = False
            self.transactions = {}
            self.fail_commit = False

        def prepare(self, transaction_id, desired_state):
            self.transactions[transaction_id] = (self.active, desired_state)
            return {"state": "PREPARED"}

        def commit(self, transaction_id):
            if self.fail_commit:
                raise RuntimeError("commit failed")
            _prior, desired = self.transactions[transaction_id]
            self.active = desired == "ACTIVE"
            return {"state": "COMMITTED"}

        def rollback(self, transaction_id):
            if transaction_id in self.transactions:
                self.active = self.transactions[transaction_id][0]
            return {"state": "ROLLED_BACK"}

        def finalize(self, transaction_id):
            self.transactions.pop(transaction_id, None)
            return {"state": "FINALIZED"}

        def verify(self):
            return {"state": "ACTIVE" if self.active else "INACTIVE"}

        def refresh(self):
            return {"state": "REFRESHED"}

    registry = ("codex", "hermes")
    with tempfile.TemporaryDirectory() as state_root:
        adapters = {name: Adapter() for name in registry}
        switch = switch_module.PortableSwitch(state_root, registry, adapters)
        if switch.status()["global"] != "INACTIVE":
            failures.append("portable switch is not inactive by default")
        codex = switch_module.resolve_targets(registry, harnesses=("codex",))
        if switch.set(codex, turn_on=True)["after"]["global"] != "PARTIAL":
            failures.append("portable harness-scoped switch failed")
        all_targets = switch_module.resolve_targets(registry, global_selector=True)
        if switch.set(all_targets, turn_on=True)["after"]["global"] != "ACTIVE":
            failures.append("portable global switch failed")
        if switch.set(("hermes",), turn_on=False)["after"]["global"] != "PARTIAL":
            failures.append("portable deactivation switch failed")
        adapters["hermes"].fail_commit = True
        failed = switch.set(("hermes",), turn_on=True)
        if failed.get("result") != "FAILED_ROLLED_BACK" or switch.status()["global"] != "PARTIAL":
            failures.append("portable switch rollback failed")
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: additive parallel feature conformance")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: additive parallel feature conformance")
    print("PASS: provider_calls=0, v1 serial fallback, inactive adaptive v2, recipient-owned completion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
