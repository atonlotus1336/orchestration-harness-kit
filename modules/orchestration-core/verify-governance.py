#!/usr/bin/env python3
"""Portable negative tests for goalposts and independent execution/review routing."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile

MODULE = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE.parents[1]))
sys.path.insert(0, str(MODULE))
from lapart_core.goalposts import create, evaluate, fingerprint, GoalpostError, record_work, status
from lapart_core.policy import CorePolicy
from lapart_core.routing import select_prime_lane
from lapart_core.review.interface import classify_review
from lapart_core.review.runner import Runner
from lapart_core.review.catalog import Catalog
from lapart_core.review.models import Pipeline, Verdict


def verify():
    failures = []
    def check(condition, label):
        if not condition:
            failures.append(label)
    policy = CorePolicy.load(MODULE / "core-policy.json")
    for task, tier in [({"multi_system": True}, "atomic"), ({}, "high_risk"),
                       ({"text": "Explain how to run autonomously"}, "atomic")]:
        check(select_prime_lane(task, tier, policy)["actual_lane"] != "T4", "unauthorized T4")
    check(select_prime_lane({"autonomous": True}, "atomic", policy)["actual_lane"] == "T4", "explicit T4 lost")
    review = classify_review({"required_level":"S0"}, declared_paths=["src/routing.py"])
    check(review["pipeline"] == "S3" and not review["review_completed"], "review floor or completion confusion")
    check(classify_review({"scope":"system"}, declared_paths=["a.py"])["requires_confirmation"], "S4 bypass")
    blocked = Runner(Catalog(), {}, ()).run(pipeline=Pipeline.S0, triggers=(),
        contents={"a.py":"pass"}, source_hashes={}, hash_provider=lambda paths: {}, providers={})
    check(blocked.verdict is Verdict.INCONCLUSIVE and not blocked.invoked_reviewers, "S0 model runner admitted")
    raw = json.loads((MODULE / "goal-contract.example.json").read_text(encoding="utf-8"))
    for invalid in [dict(raw, endpoint=[]), dict(raw, categories=raw["categories"][1:])]:
        try:
            create(invalid)
        except GoalpostError:
            pass
        else:
            failures.append("missing endpoint or usability accepted")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root/"src").mkdir()
        (root/"src/app.py").write_text("pass")
        (root/"proof.txt").write_text("Independent test fixture")
        state = create(raw, now=0)
        observations = {key: {
            "passed":True, "artifact":"proof.txt",
            "sha256":hashlib.sha256((root/"proof.txt").read_bytes()).hexdigest(),
            "input_fingerprint":fingerprint(root, raw["inputs"]),
            "method":"independent_review" if key=="clear" else "deterministic",
            "assessor_id":"reviewer",
        } for key in ["acceptance", "clear", "accessible", "behavior", "regression"]}
        observations["clear"]["assessor_id"] = "builder"
        check(evaluate(state, observations, root, now=1)["decision"] != "complete", "builder self-grading accepted")
        observations["clear"]["assessor_id"] = "reviewer"
        report = evaluate(create(raw, now=0), observations, root, now=1)
        check(report["decision"] == "complete" and report["model_calls"] == 0, "verified finite endpoint failed")
        state = create(raw, now=0)
        observations["acceptance"]["passed"] = False
        evaluate(state, observations, root, now=1)
        record_work(state,"work",1800)
        check(status(state, now=2)["due"], "active-time checkpoint absent")
        observations["regression"]["passed"] = False
        report = evaluate(state, observations, root, now=2)
        check(report["categories"]["correctness"]["regression"], "regression hidden")
        (root/"src/app.py").write_text("changed")
        check(evaluate(create(raw, now=0), observations, root, now=1)["decision"] != "complete", "stale proof accepted")
    return failures


if __name__ == "__main__":
    failures = verify()
    print("BLOCKED: " + "; ".join(failures) if failures else "PASS: portable goalpost and routing boundaries; model_calls=0")
    raise SystemExit(bool(failures))
