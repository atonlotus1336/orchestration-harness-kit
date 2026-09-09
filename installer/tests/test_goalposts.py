from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2]/'modules/orchestration-core')]
from lapart_core.goalposts import (GoalpostError, create, evaluate, fingerprint,
    local_file, locked_state, markdown, record_work, status)


@pytest.fixture(autouse=True)
def goal_owner(tmp_path, monkeypatch):
    from lapart_core.review.controller import Controller
    import lapart_core.goalposts as goals
    owner = Controller(state_root=tmp_path/'owner', vault_root=tmp_path,
        catalog_path=tmp_path/'unused.json', config={"schema_version":"1.0", "max_deliveries":2})
    monkeypatch.setattr(goals, '_evidence_owner', lambda: owner)
    return owner


def contract():
    return {"goal_id":"demo", "objective":"Deliver the agreed working feature", "builder_id":"builder",
        "inputs":["app.txt"], "endpoint":[{"id":"acceptance", "description":"Agreed task succeeds"}],
        "categories":[
            {"id":"usability", "label":"Human usability", "checks":[
                {"id":"clear", "label":"People understand the controls", "kind":"judgment", "critical":True},
                {"id":"accessible", "label":"Keyboard task completes", "kind":"deterministic"}]},
            {"id":"correctness", "label":"Correctness", "checks":[
                {"id":"behavior", "label":"Contract test", "kind":"deterministic"},
                {"id":"regression", "label":"Existing behavior", "kind":"deterministic"}]}]}


def proof(root, state, failed=(), unknown=()):
    (root/"app.txt").write_text("current source") if not (root/"app.txt").exists() else None
    artifact = root/"checks.txt"
    artifact.write_text("External verification result")
    result = {}
    for ident in ("acceptance", "clear", "accessible", "behavior", "regression"):
        if ident in unknown:
            continue
        result[ident] = {"passed":ident not in failed, "artifact":"checks.txt",
            "sha256":hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "input_fingerprint":fingerprint(root,state["contract"]["inputs"]),
            "method":"independent_review" if ident=="clear" else "deterministic", "assessor_id":"reviewer"}
    from lapart_core.goalposts import _evidence_owner
    owner = _evidence_owner()
    assessment = {"schema_version":"goal-assessment.v1", "contract_hash":state["contract_hash"],
        "input_fingerprint":fingerprint(root,state["contract"]["inputs"]),
        "checks":{key:{field:value[field] for field in ("passed", "method", "assessor_id")}
                  for key,value in result.items()}}
    artifact.write_text(json.dumps(owner._signed(assessment)))
    for value in result.values(): value['sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return result


def test_finite_endpoint_and_usability_cannot_be_omitted():
    for key in ("endpoint", "categories"):
        c=contract();c.pop(key)
        with pytest.raises(GoalpostError):create(c)
    c=contract();c["categories"][0]["id"]="visual-polish"
    with pytest.raises(GoalpostError):create(c)
    c=contract();c["categories"][0]["threshold"]=79
    with pytest.raises(GoalpostError):create(c)


def test_frozen_endpoint_cannot_drift():
    state=create(contract(),now=0)
    state["contract"]["endpoint"][0]["description"]="Invent a larger task"
    with pytest.raises(GoalpostError,match="contract changed"):status(state,now=1)


def test_active_time_is_idempotent_and_does_not_count_idle(tmp_path):
    state=create(contract(),now=0)
    evaluate(state,proof(tmp_path,state,failed=("acceptance",)),tmp_path,now=1)
    assert not status(state,now=1000)["due"]
    record_work(state,"first",1799);record_work(state,"first",1799)
    assert not status(state,now=1001)["due"]
    record_work(state,"second",1)
    assert status(state,now=1002)["due"]
    with pytest.raises(GoalpostError):record_work(state,"first",2)


def test_baseline_milestone_completion_force_checkpoint(tmp_path):
    state=create(contract(),now=0)
    assert status(state,now=1)["due"]
    evaluate(state,proof(tmp_path,state,failed=("acceptance",)),tmp_path,now=1)
    assert status(state,now=2,milestone=True)["due"]
    assert status(state,now=2,completion=True)["due"]


def test_usability_blocks_success_despite_other_perfect_scores(tmp_path):
    state=create(contract(),now=0)
    result=evaluate(state,proof(tmp_path,state,failed=("clear",)),tmp_path,now=1)
    assert result["decision"]=="iterate"
    assert result["focus_next"][0]["category"]=="usability"
    assert result["categories"]["correctness"]["score"]==100


def test_builder_self_grading_does_not_pass_usability(tmp_path):
    state=create(contract(),now=0);obs=proof(tmp_path,state)
    obs["clear"]["assessor_id"]="builder"
    result=evaluate(state,obs,tmp_path,now=1)
    assert result["categories"]["usability"]["checks"]["clear"]=="unknown"
    assert result["decision"]=="iterate"


def test_stale_source_or_artifact_invalidates_evidence(tmp_path):
    state=create(contract(),now=0);obs=proof(tmp_path,state)
    (tmp_path/"app.txt").write_text("changed source")
    result=evaluate(state,obs,tmp_path,now=1)
    assert result["categories"]["correctness"]["score"]==0
    assert result["decision"]!="complete"


def test_replayed_report_cannot_hide_deleted_proof(tmp_path):
    state=create(contract(),now=0);obs=proof(tmp_path,state,failed=("acceptance",))
    first=evaluate(state,obs,tmp_path,now=1)
    assert evaluate(state,obs,tmp_path,now=2)==first
    assert len(state["history"])==1
    (tmp_path/"checks.txt").unlink()
    second=evaluate(state,obs,tmp_path,now=3)
    assert second["categories"]["usability"]["score"]==0


def test_new_gain_cannot_mask_a_regression(tmp_path):
    state=create(contract(),now=0)
    evaluate(state,proof(tmp_path,state,failed=("acceptance","behavior")),tmp_path,now=1)
    record_work(state,"round2",1800)
    report=evaluate(state,proof(tmp_path,state,failed=("acceptance","regression")),tmp_path,now=2)
    assert report["categories"]["correctness"]["score"]==50
    assert report["categories"]["correctness"]["regression"]
    assert not report["categories"]["correctness"]["passed"]
    assert report["stagnant_rounds"]==1


def test_stop_once_endpoint_and_categories_pass(tmp_path):
    state=create(contract(),now=0)
    report=evaluate(state,proof(tmp_path,state),tmp_path,now=1)
    assert report["decision"]=="complete"
    assert status(state,now=2)["decision"]=="complete"
    with pytest.raises(GoalpostError):evaluate(state,{},tmp_path,now=2)
    assert "Stop this goal" in markdown(report)


def test_stagnation_round_limit_and_wall_clock_are_bounded(tmp_path):
    for limit in ("no_progress_limit", "max_goalposts"):
        c=contract();c[limit]=2
        state=create(c,now=0)
        obs=proof(tmp_path,state,failed=("acceptance",))
        reports=[]
        for n in range(3):
            record_work(state,str(n),1800)
            reports.append(evaluate(state,obs,tmp_path,now=n+1))
            if state["status"]!="active":break
        assert reports[-1]["decision"]=="checkpoint"
    state=create(contract(),now=0)
    assert status(state,now=14400)["decision"]=="checkpoint"
    assert evaluate(state,{},tmp_path,now=14400)["decision"]=="checkpoint"


def test_unknown_evidence_and_new_categories_cannot_claim_success(tmp_path):
    state=create(contract(),now=0)
    assert evaluate(state,{},tmp_path,now=1)["decision"]=="iterate"
    with pytest.raises(GoalpostError):evaluate(state,{"bonus":{}},tmp_path,now=2)


@pytest.mark.parametrize("value", [float("nan"),float("inf"),-1,True])
def test_invalid_work_durations_rejected(value):
    with pytest.raises(GoalpostError):record_work(create(contract()),"x",value)


def test_path_escape_and_concurrent_state_writer_rejected(tmp_path):
    for value in ("../outside", "D:/MyVault/outside", "/outside"):
        with pytest.raises(GoalpostError):local_file(tmp_path,value)
    with locked_state(tmp_path/"state.json"):
        with pytest.raises(GoalpostError):
            with locked_state(tmp_path/"state.json"):pass


def cli():
    path=Path(__file__).resolve().parents[2]/"modules/orchestration-core/goalposts.py"
    spec=importlib.util.spec_from_file_location("goalposts_cli",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.main


def test_cli_refuses_work_until_baseline_and_preserves_fixed_contract(tmp_path):
    p=tmp_path/"contract.json";p.write_text(json.dumps(contract()))
    main=cli();base=["--workspace",str(tmp_path)]
    assert main(base+["init","--contract",str(p)])==10
    assert main(base+["run-work","--event-id","w","--",sys.executable,"-c","raise RuntimeError()"] )==10
    assert main(base+["init","--contract",str(p)])==2


def test_cli_work_failure_requires_evaluation_before_next_work(tmp_path):
    main=cli();p=tmp_path/"contract.json";p.write_text(json.dumps(contract()))
    base=["--workspace",str(tmp_path)];main(base+["init","--contract",str(p)])
    state=json.loads((tmp_path/".goalposts/state.json").read_text())
    obs=tmp_path/"observations.json";obs.write_text(json.dumps(proof(tmp_path,state,failed=("acceptance",))))
    assert main(base+["evaluate","--observations",str(obs)])==20
    assert main(base+["run-work","--event-id","fail","--",sys.executable,"-c","raise SystemExit(1)"])==10
    assert main(base+["status"])==10
    assert main(base+["run-work","--event-id","again","--",sys.executable,"-c","pass"])==10


def test_evidence_replay_cannot_bypass_wall_clock_ceiling(tmp_path):
    state=create(contract(),now=0)
    obs=proof(tmp_path,state,failed=("acceptance",))
    assert evaluate(state,obs,tmp_path,now=1)["decision"]=="iterate"
    assert evaluate(state,obs,tmp_path,now=14400)["decision"]=="checkpoint"


def test_cli_records_work_and_refuses_duplicate_side_effects(tmp_path):
    main=cli();p=tmp_path/"contract.json";p.write_text(json.dumps(contract()))
    base=["--workspace",str(tmp_path)];main(base+["init","--contract",str(p)])
    state=json.loads((tmp_path/".goalposts/state.json").read_text())
    obs=tmp_path/"observations.json";obs.write_text(json.dumps(proof(tmp_path,state,failed=("acceptance",))))
    assert main(base+["evaluate","--observations",str(obs)])==20
    assert main(base+["run-work","--event-id","once","--",sys.executable,"-c","pass"])==0
    assert main(base+["run-work","--event-id","once","--",sys.executable,"-c","pass"])==2
    state=json.loads((tmp_path/".goalposts/state.json").read_text())
    assert state["active_seconds"]>0


def test_ui_test_counts_alone_are_not_readability_judgment():
    c=contract();c["categories"][0]["checks"][0]["kind"]="deterministic"
    with pytest.raises(GoalpostError,match="independent"):create(c)


def test_milestone_has_distinct_evidence_but_repeat_is_idempotent(tmp_path):
    state=create(contract(),now=0);obs=proof(tmp_path,state,failed=("acceptance",))
    first=evaluate(state,obs,tmp_path,now=1)
    milestone=evaluate(state,obs,tmp_path,now=2,milestone=True)
    assert milestone["trigger"]=="milestone" and milestone["evidence_id"]!=first["evidence_id"]
    repeat=evaluate(state,obs,tmp_path,now=3,milestone=True)
    assert repeat==milestone and len(state["history"])==2


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_interrupted_work_requires_evaluation(tmp_path, monkeypatch, error):
    main = cli()
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract()))
    base = ["--workspace", str(tmp_path)]
    main(base + ["init", "--contract", str(contract_path)])
    path = tmp_path / ".goalposts/state.json"
    state = json.loads(path.read_text())
    obs = tmp_path / "observations.json"
    obs.write_text(json.dumps(proof(tmp_path, state, failed=("acceptance",))))
    assert main(base + ["evaluate", "--observations", str(obs)]) == 20
    calls = []
    def interrupt(*args, **kwargs):
        calls.append(args)
        raise error()
    monkeypatch.setattr(main.__globals__["subprocess"], "run", interrupt)
    with pytest.raises(error):
        main(base + ["run-work", "--event-id", "interrupted", "--", "fixture"])
    state = json.loads(path.read_text())
    assert state["pending_evaluation"]
    assert state["work_events"]["interrupted"] >= 0
    assert main(base + ["run-work", "--event-id", "new", "--", "fixture"]) == 10
    assert len(calls) == 1


def test_category_gains_survive_other_category_regression(tmp_path):
    spec = contract()
    spec["categories"][1]["checks"][1]["weight"] = 4
    state = create(spec, now=0)
    evaluate(state, proof(tmp_path, state, failed=("acceptance", "behavior")), tmp_path, now=1)
    record_work(state, "second", 1)
    evaluate(state, proof(tmp_path, state, failed=("clear",)), tmp_path, now=2)
    assert "behavior" in state["passed_checks"]
    assert state["floors"]["correctness"] == 100
    assert "acceptance" in state["passed_endpoint"]
    record_work(state, "third", 1)
    report = evaluate(state, proof(tmp_path, state, failed=("behavior",)), tmp_path, now=3)
    assert report["decision"] != "complete"
    assert report["categories"]["correctness"]["regression"]
    assert report["categories"]["correctness"]["score"] == 80


@pytest.mark.parametrize('error', [FileNotFoundError, PermissionError])
def test_launch_error_requires_evaluation(tmp_path, monkeypatch, error):
    main=cli();p=tmp_path/'contract.json';p.write_text(json.dumps(contract()))
    base=['--workspace',str(tmp_path)];main(base+['init','--contract',str(p)])
    state_path=tmp_path/'.goalposts/state.json';state=json.loads(state_path.read_text())
    obs=tmp_path/'observations.json';obs.write_text(json.dumps(proof(tmp_path,state,failed=('acceptance',))))
    assert main(base+['evaluate','--observations',str(obs)])==20
    def fail(*a,**k):raise error('fixture launch refusal')
    import subprocess
    monkeypatch.setattr(subprocess,'run',fail)
    assert main(base+['run-work','--event-id','launch','--','fixture'])==10
    after=json.loads(state_path.read_text())
    assert after['pending_evaluation'] and after['work_events']['launch'] is not None
    assert main(base+['run-work','--event-id','next','--','fixture'])==10
