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
from lapart_core.review.controller import Controller
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
        owner = Controller(state_root=root/'fixture-owner', vault_root=root,
            catalog_path=root/'unused.json', config={"schema_version":"1.0", "max_deliveries":2})
        state = create(raw, now=0)
        def signed_fixture(observations):
            # Synthetic fixture only; not real assessment issuance.
            assessment={"schema_version":"goal-assessment.v1", "contract_hash":state["contract_hash"],
                "input_fingerprint":fingerprint(root,raw["inputs"]),
                "checks":{k:{f:v[f] for f in ("passed","method","assessor_id")} for k,v in observations.items()}}
            (root/'proof.txt').write_text(json.dumps(owner._signed(assessment)))
            for v in observations.values():v['sha256']=hashlib.sha256((root/'proof.txt').read_bytes()).hexdigest()
        observations = {key: {
            "passed":True, "artifact":"proof.txt",
            "sha256":hashlib.sha256((root/"proof.txt").read_bytes()).hexdigest(),
            "input_fingerprint":fingerprint(root, raw["inputs"]),
            "method":"independent_review" if key=="clear" else "deterministic",
            "assessor_id":"reviewer",
        } for key in ["acceptance", "clear", "accessible", "behavior", "regression"]}
        check(evaluate(create(raw,now=0),observations,root,now=1,evidence_owner=owner)["decision"] != "complete", "unsigned proof accepted")
        observations["clear"]["assessor_id"] = "builder"
        signed_fixture(observations)
        check(evaluate(state, observations, root, now=1, evidence_owner=owner)["decision"] != "complete", "builder self-grading accepted")
        observations["clear"]["assessor_id"] = "reviewer"
        signed_fixture(observations)
        report = evaluate(create(raw, now=0), observations, root, now=1, evidence_owner=owner)
        check(report["decision"] == "complete" and report["model_calls"] == 0, "verified finite endpoint failed")
        state = create(raw, now=0)
        observations["acceptance"]["passed"] = False
        signed_fixture(observations)
        evaluate(state, observations, root, now=1, evidence_owner=owner)
        record_work(state,"work",1800)
        check(status(state, now=2)["due"], "active-time checkpoint absent")
        observations["regression"]["passed"] = False
        signed_fixture(observations)
        report = evaluate(state, observations, root, now=2, evidence_owner=owner)
        check(report["categories"]["correctness"]["regression"], "regression hidden")
        tampered=json.loads((root/'proof.txt').read_text());tampered['data']['checks']['acceptance']['passed']=True
        (root/'proof.txt').write_text(json.dumps(tampered))
        for v in observations.values():v['sha256']=hashlib.sha256((root/'proof.txt').read_bytes()).hexdigest()
        check(evaluate(create(raw,now=0),observations,root,now=1,evidence_owner=owner)['categories']['correctness']['score']==0, 'tampered signature accepted')
        signed_fixture(observations)
        (root/"src/app.py").write_text("changed")
        check(evaluate(create(raw, now=0), observations, root, now=1, evidence_owner=owner)["decision"] != "complete", "stale proof accepted")
    return failures


if __name__ == "__main__":
    failures = verify()
    print("BLOCKED: " + "; ".join(failures) if failures else "PASS: portable goalpost and routing boundaries; model_calls=0")
    raise SystemExit(bool(failures))
