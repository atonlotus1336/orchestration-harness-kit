"""Shared adversarial contract: real local HTTP worker, no external models."""
from pathlib import Path
import hashlib,importlib,json,sys,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from dataclasses import replace
import pytest
ROOT=Path(__file__).resolve().parents[2]
if (ROOT/'prime_bridge').is_dir():
    sys.path.insert(0,str(ROOT));PACKAGE='prime_bridge.adaptive_review'
else:
    sys.path[:0]=[str(ROOT),str(ROOT/'modules/orchestration-core')];PACKAGE='lapart_core.review'
a=importlib.import_module(PACKAGE+'.adapters'); c=importlib.import_module(PACKAGE+'.catalog')
m=importlib.import_module(PACKAGE+'.models'); rt=importlib.import_module(PACKAGE+'.runner')
t=importlib.import_module(PACKAGE+'.transport'); router=importlib.import_module(PACKAGE+'.router')

@pytest.fixture
def server():
    state={'requests':[],'delay':0,'status':200,'response':{'choices':[{'message':{'content':'[]'}}],'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            state['requests'].append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if state.get('on_request'):state['on_request']()
            time.sleep(state['delay'])
            self.send_response(state['status'])
            if state['status']==302:self.send_header('Location','/redirected')
            self.end_headers()
            try:self.wfile.write(json.dumps(state['response']).encode())
            except (BrokenPipeError,ConnectionResetError):pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.daemon_threads=True
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    state['endpoint']=f'http://127.0.0.1:{http.server_port}/v1/chat/completions'
    yield state
    http.shutdown();http.server_close();thread.join()

def fixture(tmp_path,server,pipeline='S1'):
    skill=tmp_path/'skill.md';skill.write_text('Find correctness defects. Report only.')
    contents={'example.py':'value = 1\n'};hashes={k:hashlib.sha256(v.encode()).hexdigest() for k,v in contents.items()}
    ids={'S1':['ponytail-review'],'S2':['evidence-integrity-audit','ponytail-review'],'S4':['improve','ponytail-audit','ponytail-debt']}[pipeline]
    cat=c.Catalog();providers={}
    for name in ids:
        cat.register(c.Reviewer(name,'changed_scope_simplicity',(),str(skill),hashlib.sha256(skill.read_bytes()).hexdigest()),a.ReviewerCapability(tuple(contents)))
        providers[name]=a.ReportOnlyProvider(transport=t.JsonReviewTransport(server['endpoint'],'synthetic'))
    args=dict(pipeline=m.Pipeline(pipeline),triggers=(),contents=contents,source_hashes=hashes,hash_provider=lambda paths:dict(hashes),providers=providers)
    return rt.Runner(cat,{},()),args

def test_owned_transport_positive_control(tmp_path,server):
    runner,args=fixture(tmp_path,server)
    result=runner.run(**args)
    assert result.verdict is m.Verdict.PASS
    assert result.invoked_reviewers==('ponytail-review',)
    assert result.budget['model_requests']==1 and result.budget['total_units']==12
    assert result.budget['usage_complete'] and len(server['requests'])==1
    assert 'tools' not in server['requests'][0]

@pytest.mark.parametrize('fault',['optional_bad_pin','missing_provider','unsafe_provider','callback','content_mismatch','stale_source','oversized_context','file_limit','oversized_skill','estimated_input'])
def test_whole_set_preflight_makes_zero_requests(tmp_path,server,monkeypatch,fault):
    runner,args=fixture(tmp_path,server,'S2')
    key='ponytail-review'
    if fault=='optional_bad_pin':runner.catalog.reviewers[key]=replace(runner.catalog.reviewers[key],required=False,skill_sha256='0'*64)
    elif fault=='missing_provider':args['providers'].pop(key)
    elif fault=='unsafe_provider':args['providers'][key]=replace(args['providers'][key],attestation=a.ProviderAttestation(tool_names=('shell',)))
    elif fault=='callback':args['providers'][key]=a.ReportOnlyProvider(lambda ctx:pytest.fail('callback must not run'))
    elif fault=='content_mismatch':args['contents']={'example.py':'different bytes'}
    elif fault=='stale_source':args['hash_provider']=lambda paths:{'example.py':'0'*64}
    elif fault in ('oversized_context','file_limit'):
        contents={'example.py':'x'*100000} if fault=='oversized_context' else {f'f{i}.py':'x' for i in range(16)}
        args['contents']=contents;args['source_hashes']={k:hashlib.sha256(v.encode()).hexdigest() for k,v in contents.items()}
        args['hash_provider']=lambda paths:dict(args['source_hashes'])
    elif fault=='oversized_skill':
        path=Path(runner.catalog.reviewers[key].skill_path);path.write_text('x'*70000)
        for name in runner.catalog.reviewers:runner.catalog.reviewers[name]=replace(runner.catalog.reviewers[name],skill_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    elif fault=='estimated_input':
        loaded=rt.load_policy();data=json.loads(json.dumps(loaded.data));data['budgets']['S2']['max_estimated_input_tokens']=1
        monkeypatch.setattr(rt,'load_policy',lambda:replace(loaded,data=data))
    result=runner.run(**args)
    assert result.verdict is m.Verdict.INCONCLUSIVE
    assert result.invoked_reviewers==() and server['requests']==[]

def test_post_review_mutation_is_rejected(tmp_path,server):
    runner,args=fixture(tmp_path,server)
    live=dict(args['source_hashes']);args['hash_provider']=lambda paths:dict(live)
    server['on_request']=lambda:live.update({'example.py':'0'*64})
    result=runner.run(**args)
    assert len(server['requests'])==1 and result.verdict is m.Verdict.INCONCLUSIVE

def limits(**overrides):
    return dict(rt.load_policy().data['budgets']['S1'])|overrides

@pytest.mark.parametrize('usage',[None,{}, {'prompt_tokens':True,'completion_tokens':2,'total_tokens':3}, {'prompt_tokens':10,'completion_tokens':2,'total_tokens':99}])
def test_missing_or_invalid_usage_stops_next_reviewer(tmp_path,server,usage):
    runner,args=fixture(tmp_path,server,'S2');server['response']['usage']=usage
    result=runner.run(**args)
    assert result.verdict is m.Verdict.INCONCLUSIVE and len(server['requests'])==1
    assert not result.budget['usage_complete'] and result.budget['total_units'] is None

@pytest.mark.parametrize('fault',['tool_call','patch','non_array','over_output','http_error','redirect'])
def test_invalid_transport_result_cannot_pass(tmp_path,server,fault):
    runner,args=fixture(tmp_path,server,'S2')
    message=server['response']['choices'][0]['message']
    if fault=='tool_call':message['tool_calls']=[{'id':'forbidden'}]
    elif fault=='patch':message['content']='[{"patch":"forbidden"}]'
    elif fault=='non_array':message['content']='{}'
    elif fault=='over_output':server['response']['usage']={'prompt_tokens':10,'completion_tokens':2049,'total_tokens':2059}
    elif fault=='http_error':server['status']=500
    elif fault=='redirect':server['status']=302
    result=runner.run(**args)
    assert result.verdict is m.Verdict.INCONCLUSIVE and len(server['requests'])==1
    assert result.budget['failed'] and result.budget['automatic_retries']==0

def test_request_budget_is_shared_and_reserves_before_send(server):
    transport=t.JsonReviewTransport(server['endpoint'],'synthetic');budget=t.ReviewBudgetTracker(limits())
    transport.run('Review',{'example.py':'x'},budget)
    with pytest.raises(t.BudgetViolation,match='request budget'):
        transport.run('Review',{'example.py':'x'},budget)
    assert len(server['requests'])==1

def test_total_budget_admission_refuses_without_request(server):
    transport=t.JsonReviewTransport(server['endpoint'],'synthetic');budget=t.ReviewBudgetTracker(limits(max_total_tokens=1))
    with pytest.raises(t.BudgetViolation,match='total budget'):
        transport.run('Review',{'example.py':'x'},budget)
    assert not server['requests']

def test_deadline_terminates_real_worker(server,monkeypatch):
    server['delay']=2
    budget=t.ReviewBudgetTracker(limits(max_elapsed_seconds=1))
    transport=t.JsonReviewTransport(server['endpoint'],'synthetic')
    spawned=[];original=t.subprocess.Popen
    def capture(*args,**kwargs):
        process=original(*args,**kwargs);spawned.append(process);return process
    monkeypatch.setattr(t.subprocess,'Popen',capture)
    started=time.monotonic()
    with pytest.raises(t.BudgetViolation):transport.run('Review',{'example.py':'x'},budget)
    assert time.monotonic()-started<1.8
    assert spawned and all(p.poll() is not None for p in spawned)
    assert budget.failed and not budget.complete

@pytest.mark.parametrize('pipeline',['S0','S4'])
def test_no_implicit_model_review(tmp_path,server,pipeline):
    runner,args=fixture(tmp_path,server,'S4' if pipeline=='S4' else 'S1');args['pipeline']=m.Pipeline(pipeline)
    result=runner.run(**args)
    assert result.verdict is m.Verdict.INCONCLUSIVE and not server['requests']

def test_typed_facts_require_complete_s0_evidence():
    policy=rt.load_policy()
    def select(raw=None,lines=1,subsystems=1):
        facts=router.structured_review_facts(raw,changed_lines=lines,subsystem_count=subsystems)
        return router.select_review(facts,policy,m.Scope.PHASE).pipeline
    good={k:True for k in router.S0_FACTS}
    assert select(good) is m.Pipeline.S0
    assert select() is m.Pipeline.S1 and select(good,lines=None) is m.Pipeline.S1
    for key in good:
        assert select(good|{key:False}) is m.Pipeline.S1
    assert select(good|{'security':True}) is m.Pipeline.S3
    with pytest.raises(ValueError):select({'mechanical_or_exact':'yes'})
    with pytest.raises(ValueError):select({'irrelevant':True})

def test_prompt_has_correctness_exact_schema_and_owned_identity(tmp_path,server):
    runner,args=fixture(tmp_path,server)
    result=runner.run(**args)
    assert result.verdict is m.Verdict.PASS
    prompt=server["requests"][0]["messages"][0]["content"]
    assert "Always check correctness" in prompt
    assert '"additionalProperties":false' in prompt
    assert "Use reviewer_id exactly ponytail-review" in prompt
    assert len(prompt.encode()) < 4000

def test_model_cannot_spoof_reviewer_identity(tmp_path,server):
    runner,args=fixture(tmp_path,server)
    server["response"]["choices"][0]["message"]["content"]=json.dumps([{
        "schema_version":"1.0","finding_id":"F1","reviewer_id":"invented",
        "severity":"HIGH","category":"correctness","title":"Wrong value",
        "evidence":["example.py:1"],"advisory":True,"blocking_proof":None}])
    result=runner.run(**args)
    assert result.verdict is m.Verdict.INCONCLUSIVE
    assert "reviewer identity mismatch" in result.reconciliation.errors

@pytest.mark.parametrize("fault",["none","passing","exception","empty","tampered","wrong_reference","no_verifier","stale_source","no_tool_budget"])
def test_blocking_requires_real_pinned_host_assertion(tmp_path,server,monkeypatch,fault):
    proofs=importlib.import_module(PACKAGE+".proofs")
    runner,args=fixture(tmp_path,server)
    (tmp_path/"example.py").write_text(args["contents"]["example.py"])
    test=tmp_path/"test_acceptance.py"
    body='import runpy\nfrom pathlib import Path\ndef test_acceptance():\n    source = runpy.run_path(str(Path(__file__).with_name("example.py")))\n    assert source["value"] == 2\n'
    if fault=="passing":body=body.replace('== 2','== 1')
    if fault=="exception":body='def test_acceptance():\n    raise RuntimeError("not an acceptance failure")\n'
    if fault=="empty":body="value = 1\n"
    test.write_text(body)
    check=proofs.HostCheck(test.name,hashlib.sha256(test.read_bytes()).hexdigest(),"Acceptance must match its independent expected value.")
    verifier=proofs.HostTestVerifier(tmp_path,{"acceptance":check},args["source_hashes"])
    args["proof_verifier"]=verifier
    if fault=="tampered":test.write_text(body+"# tampered")
    if fault=="stale_source":(tmp_path/"example.py").write_text("changed")
    if fault=="no_verifier":args.pop("proof_verifier")
    if fault=="no_tool_budget":
        loaded=rt.load_policy(); data=json.loads(json.dumps(loaded.data))
        data["budgets"]["S1"]["max_tool_calls"]=0
        monkeypatch.setattr(rt,"load_policy",lambda:replace(loaded,data=data))
    finding={"schema_version":"1.0","finding_id":"F1","reviewer_id":"ponytail-review",
       "severity":"HIGH","category":"correctness","title":"Acceptance mismatch",
       "evidence":["example.py:1","check:missing" if fault=="wrong_reference" else "check:acceptance"],
       "advisory":False,"blocking_proof":proofs.PROOF}
    server["response"]["choices"][0]["message"]["content"]=json.dumps([finding])
    runner.blocking_proofs=(proofs.PROOF,)
    result=runner.run(**args)
    if fault=="none":
        assert result.reconciliation.blocking
        assert result.proof_receipts[0]["failures"]==1
        assert result.budget["tool_calls"]==1
    else:
        assert not result.reconciliation or not result.reconciliation.blocking
    if fault in ("tampered","stale_source"):
        assert server["requests"]==[]
    if fault in ("no_tool_budget",):
        assert result.verdict is m.Verdict.INCONCLUSIVE

def test_reasoning_setting_is_explicit_and_budgeted(server):
    transport=t.JsonReviewTransport(server["endpoint"],"synthetic",reasoning_effort="low")
    budget=t.ReviewBudgetTracker(limits())
    transport.run("Review",{"example.py":"x"},budget)
    assert server["requests"][0]["reasoning_effort"]=="low"
    assert budget.requests==1
    default=t.JsonReviewTransport(server["endpoint"],"synthetic")
    assert "reasoning_effort" not in json.loads(default.payload("Review",{"example.py":"x"},10))
    with pytest.raises(t.BudgetViolation):
        t.JsonReviewTransport(server["endpoint"],"synthetic",reasoning_effort="unbounded").validate()

def test_truncated_response_cannot_pass_even_with_valid_empty_array(tmp_path,server):
    runner,args=fixture(tmp_path,server)
    server["response"]["choices"][0]["finish_reason"]="length"
    result=runner.run(**args)
    assert result.verdict is m.Verdict.INCONCLUSIVE
    assert result.budget["last_finish_reason"]=="length"

def test_caller_cannot_weaken_owned_finding_schema(tmp_path,server):
    runner,args=fixture(tmp_path,server)
    unsafe=rt.Runner(runner.catalog,{"type":"object"},())
    result=unsafe.run(**args)
    assert result.verdict is m.Verdict.INCONCLUSIVE
    assert not server["requests"]
