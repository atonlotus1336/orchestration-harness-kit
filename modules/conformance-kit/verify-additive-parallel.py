#!/usr/bin/env python3
"""Provider-free feature conformance separate from the closed protocol suite."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "modules/additive-parallel"
SCENARIOS = Path(__file__).resolve().with_name("additive-parallel-scenarios.json")


def load_feature():
    spec = importlib.util.spec_from_file_location("additive_parallel", MODULE / "verify-additive-parallel.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def verify() -> list[str]:
    feature = load_feature()
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
    observation = feature.observe_events("job", [{"event_id": "ok", "job_id": "job", "status": "running"}, {"event_id": "late", "job_id": "job", "status": "cancelled"}, {"event_id": "wrong", "job_id": "other", "status": "done"}])
    if observation["canonical_completion"] is not False or observation["rejected"] != ["wrong"]:
        failures.append("observation-only completion boundary failed")
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: additive parallel feature conformance")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: additive parallel feature conformance")
    print("PASS: provider_calls=0, serial fallback, parent-only completion, Orca observation-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
