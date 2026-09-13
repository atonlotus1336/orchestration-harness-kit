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
    state={'requests':[],'delay':0,'status':200,'response':{'choices':[{'finish_reason':'stop','message':{'content':'[]'}}],'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}}
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

controller_module=importlib.import_module(PACKAGE+'.controller')
policy_module=importlib.import_module(PACKAGE+'.policy')

def setup_controller(tmp_path,server,mode='enforced',pipeline='S1'):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'example.py').write_text('value = 1\n')
    skill=tmp_path/'skill.md';skill.write_text('Review correctness only.')
    ids=['ponytail-review','evidence-integrity-audit','improve-codebase-architecture','improve','ponytail-audit','ponytail-debt']
    cat=tmp_path/'catalog.json'
    cat.write_text(json.dumps({'schema_version':'1.0','reviewers':[
        {'reviewer_id':key,'lens':'changed_scope_simplicity','triggers':[],'skill_path':'skill.md',
         'skill_sha256':hashlib.sha256(skill.read_bytes()).hexdigest(),
         'requires_confirmation':key in ['improve','ponytail-audit','ponytail-debt']} for key in ids]}))
    config={'schema_version':'1.0','max_deliveries':2,'harness_modes':{'example':mode},
        'approved_workspaces':[str(repo)],'provider':{'endpoint':server['endpoint'],'model':'synthetic'}}
    ctl=controller_module.Controller(state_root=tmp_path/'state',vault_root=tmp_path,catalog_path=cat,config=config)
    ev={'stage':'finished','execution_evidence_status':'VERIFIED','harness':'example','run_id':'one',
        'after':{'identity':'source'},'binding':{'packet_hash':'packet','session_binding_sha256':'session'},
        'before':{'contents':{'example.py':hashlib.sha256((repo/'example.py').read_bytes()).hexdigest()}},
        'packet':{'allowed_paths':['example.py'],'objective':'one task','invariants':['Preserve unrelated code'],
            'change_contract':{'objective':'one task','acceptance_criteria':['Requested behavior works']}},'review_pipeline':'S0','review_verdict':'PASS'}
    args=dict(workspace=repo,evidence=ev,pipeline=pipeline,scope='phase',current_identity=lambda:'source')
    return ctl,args

@pytest.mark.parametrize('pipeline,calls',[('S0',0),('S1',1),('S2',2),('S3',3),('S4',3)])
def test_controller_completes_once_and_reuses_receipt(tmp_path,server,pipeline,calls):
    ctl,args=setup_controller(tmp_path,server,pipeline=pipeline)
    if pipeline=='S4':args.update(scope='system',s4_confirmed=True)
    first=ctl.run(**args)
    assert first['review_verdict']=='PASS',first
    assert first['model_calls']==calls
    assert first['review_completed'] and not first['consumption']['review_blocks_completion']
    assert ctl._read(Path(first['receipt_path']))['source_identity']=='source'
    again=ctl.run(**args)
    assert again['cache_hit'] and again['model_calls']==0
    assert len(server['requests'])==calls


def test_optional_parallel_feedback_import_failure_does_not_erase_review(
    tmp_path, server, monkeypatch,
):
    ctl, args = setup_controller(tmp_path, server)
    real_import = __import__

    def guarded_import(name, *import_args, **import_kwargs):
        if name.endswith("parallel_feedback"):
            raise ImportError("optional feedback adapter unavailable")
        return real_import(name, *import_args, **import_kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    result = ctl.run(**args)
    assert result["review_verdict"] == "PASS"
    assert result["review_completed"] is True
    assert result["adaptive_parallel_feedback"] == {
        "status": "UNAVAILABLE", "promotion_performed": False,
    }
    assert len(server["requests"]) == 1


@pytest.mark.parametrize('fault',['shadow','unapproved','unconfirmed_s4','stale','bad_evidence','missing_provider','bad_skill','oversized'])
def test_controller_refuses_without_requests(tmp_path,server,fault):
    ctl,args=setup_controller(tmp_path,server)
    if fault=='shadow':ctl.config['harness_modes']['example']='shadow'
    elif fault=='unapproved':ctl.config['approved_workspaces']=[]
    elif fault=='unconfirmed_s4':args.update(pipeline='S4',scope='system')
    elif fault=='stale':args['current_identity']=lambda:'changed'
    elif fault=='bad_evidence':args['evidence']['execution_evidence_status']='FAILED'
    elif fault=='missing_provider':ctl.config['provider']={}
    elif fault=='bad_skill':(tmp_path/'skill.md').write_text('changed')
    elif fault=='oversized':(args['workspace']/'example.py').write_text('x'*40000)
    value=ctl.run(**args)
    assert value['review_verdict']=='INCONCLUSIVE'
    assert len(server['requests'])==0

def test_owner_zero_usage_refusal_can_recover_same_delivery(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    ctl.config['approved_workspaces']=[]
    refused=ctl.run(**args)
    assert refused['model_calls']==0
    ctl.config['approved_workspaces']=[str(args['workspace'])]
    result=ctl.recover_refusal(block_id=refused['block_id'], **args)
    assert result['review_verdict']=='PASS' and len(server['requests'])==1
    resolution=ctl.resolve_block(block_id=refused['block_id'], **{k:args[k] for k in ('workspace','evidence','scope','current_identity')})
    assert resolution['original_run_id']==resolution['replacement_run_id']=='one'
    assert ctl._read(ctl.state/'blocks'/(refused['block_id']+'.json'))['review']['review_verdict']=='INCONCLUSIVE'


@pytest.mark.parametrize('fault',['task','session','scope','harness','workspace','lower_depth','altered','unknown_usage'])
def test_owner_refusal_recovery_rejects_unbound_or_unsafe(tmp_path,server,fault):
    ctl,args=setup_controller(tmp_path,server)
    ctl.config['approved_workspaces']=[]
    refused=ctl.run(**args)
    ctl.config['approved_workspaces']=[str(args['workspace'])]
    if fault=='task':args['evidence']['packet']['objective']='unrelated'
    elif fault=='session':args['evidence']['binding']['session_binding_sha256']='another'
    elif fault=='scope':args['evidence']['packet']['allowed_paths']=['else.py']
    elif fault=='harness':args['evidence']['harness']='other'
    elif fault=='workspace':args['workspace']=tmp_path
    elif fault=='lower_depth':args['pipeline']='S0'
    elif fault=='altered':
        path=ctl.state/'blocks'/(refused['block_id']+'.json')
        data=json.loads(path.read_text());data['data']['review']['reason']='altered';path.write_text(json.dumps(data))
    elif fault=='unknown_usage':
        block=ctl._read(ctl.state/'blocks'/(refused['block_id']+'.json'))
        controller_module.atomic(ctl.state/'lineages'/(block['lineage']+'.json'),ctl._signed({'unknown_usage':True}))
    with pytest.raises(ValueError):ctl.recover_refusal(block_id=refused['block_id'], **args)
    assert not server['requests']


@pytest.mark.parametrize('fault',[None,'unrelated_run','lower_depth','altered_proof','changed_scope','unknown_usage','same_source','same_run','changed_authority','missing_original_check','still_fails','skipped','empty','capability_refused','tool_exhausted','pending_retest','interrupt','live_pass','live_fail'])
def test_signed_finding_resolution_contract(tmp_path,server,fault,monkeypatch):
    # Signed-state fixture represents an existing owner's finding/review receipts.
    # No caller-reported review or test status is accepted by the API under test.
    import copy
    ctl,args=setup_controller(tmp_path,server)
    check=args['workspace']/'test_acceptance.py';check.write_text("from pathlib import Path\nimport runpy\ndef test_value(): assert runpy.run_path(str(Path(__file__).parent/'example.py'))['value']==2\n")
    checksum=hashlib.sha256(check.read_bytes()).hexdigest()
    ctl.config['checks']={str(args['workspace']):{'acceptance':{'test_path':check.name,'sha256':checksum,'description':'original acceptance'}}}
    # Synthetic constrained-executor output, not live harness certification.
    import types
    executor=types.ModuleType(PACKAGE.rsplit('.',1)[0]+'.evidence_adapter'); executions=[]
    def execute(workspace,path,xml,config,timeout):
        executions.append(path)
        if fault=='interrupt':raise KeyboardInterrupt()
        if fault=='capability_refused':return {'status':'CAPABILITY_REFUSED'}
        body='<testcase name="original" />'
        if fault=='still_fails':body='<testcase><failure message="assert False" /></testcase>'
        if fault=='skipped':body='<testcase><skipped /></testcase>'
        if fault=='empty':body=''
        xml.write_text('<testsuite>'+body+'</testsuite>')
        return {'status':'ok','exit_code':1 if fault=='still_fails' else 0}
    executor.run_host_acceptance=execute
    if fault not in ('live_pass','live_fail'):
        monkeypatch.setattr(importlib.import_module(executor.__name__), 'run_host_acceptance', execute)
    else:
        native=importlib.import_module(executor.__name__);real=native.run_host_acceptance
        def tracked(*args,**kwargs):
            executions.append(args[1]);return real(*args,**kwargs)
        monkeypatch.setattr(native,'run_host_acceptance',tracked)
    original=ctl.run(**args)
    original.update(review_verdict='FINDINGS', findings=[{'finding_id':'F1','blocking_proof':'reproducible_failed_test_tied_to_acceptance','evidence':['check:acceptance']}],
        finding_counts={'total':1,'blocking':1},consumption={'review_passed':False,'review_blocks_completion':True,'advisory':False})
    original['proof_receipts']=[{'check_id':'acceptance','test_sha256':checksum,'verified':True,'tests':1}]
    if fault=='missing_original_check':original['proof_receipts']=[]
    controller_module.atomic(Path(original['receipt_path']),ctl._signed(original))
    blocked=ctl.run(**args);block_id=blocked['block_id']
    original_bytes=Path(original['receipt_path']).read_bytes()
    evidence=copy.deepcopy(args['evidence']);evidence['run_id']='two';evidence['after']['identity']='fresh'
    if fault=='live_pass':(args['workspace']/'example.py').write_text('value = 2\n')
    replacement=copy.deepcopy(original)
    replacement.update(review_verdict='PASS',findings=[],finding_counts={'total':0,'blocking':0},
        consumption={'review_passed':True,'review_blocks_completion':False,'advisory':False},
        source_identity='fresh',review_sequence=2,delivery={**original['delivery'],'run_id':'two'})
    replacement_path=ctl.state/'receipts'/(controller_module.digest(replacement['delivery'])+'.json')
    replacement['receipt_path']=str(replacement_path)
    ledger_path=ctl.state/'lineages'/(original['lineage']+'.json')
    ledger=ctl._read(ledger_path);ledger['deliveries']=2
    ledger['spent']['model_requests']=2  # Existing owner's settled cumulative spend.
    if fault=='unknown_usage':ledger['unknown_usage']=True
    if fault=='tool_exhausted':ledger['spent']['tool_calls']=ledger['limits']['max_tool_calls']
    if fault=='pending_retest':ledger['pending_retest']='earlier interrupted attempt'
    controller_module.atomic(ledger_path,ctl._signed(ledger));ledger_bytes=ledger_path.read_bytes()
    if fault=='unrelated_run':replacement['resolution_binding']['task']='another task'
    elif fault=='lower_depth':replacement['review_pipeline']='S0'
    elif fault=='changed_scope':evidence['packet']['allowed_paths']=['other.py']
    elif fault=='same_source':evidence['after']['identity']='source';replacement['source_identity']='source'
    elif fault=='same_run':evidence['run_id']='one'
    elif fault=='changed_authority':ctl.config['checks']={}
    controller_module.atomic(replacement_path,ctl._signed(replacement))
    if fault=='altered_proof':
        value=json.loads(Path(original['receipt_path']).read_text());value['data']['findings'][0]['finding_id']='changed'
        Path(original['receipt_path']).write_text(json.dumps(value))
    call=lambda:ctl.resolve_block(block_id=block_id,workspace=args['workspace'],evidence=evidence,
        scope='phase',current_identity=lambda:evidence['after']['identity'])
    if fault and fault!='live_pass':
        with pytest.raises(KeyboardInterrupt if fault=='interrupt' else ValueError):call()
    else:
        resolution=call()
        assert resolution['finding_ids']==['F1'] and resolution['original_run_id']=='one'
        assert resolution['replacement_run_id']=='two'
        assert Path(original['receipt_path']).read_bytes()==original_bytes
        assert ctl._read(ctl.state/'resolutions'/(block_id+'.json'))==resolution
        assert resolution['retest_receipts'][0]['passed'] is True
        assert call()==resolution and len(executions)==1  # Idempotent, no additional charge.
    if executions:
        after=ctl._read(ledger_path)
        if fault=='interrupt':
            assert after['pending_retest']
            with pytest.raises(ValueError,match='interrupted retest'):call()
        else:
            assert after['spent']['tool_calls']==ledger['spent'].get('tool_calls',0)+1
            assert 'pending_retest' not in after
    else:assert ledger_path.read_bytes()==ledger_bytes
    assert len(server['requests'])==1  # Resolution never invokes a model.


def test_tampered_cache_cannot_pass(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    first=ctl.run(**args);path=Path(first['receipt_path'])
    envelope=json.loads(path.read_text());envelope['data']['review_verdict']='FINDINGS'
    path.write_text(json.dumps(envelope))
    assert ctl.run(**args)['review_verdict']=='INCONCLUSIVE'
    assert len(server['requests'])==1

def test_pending_delivery_never_replays_after_interrupt(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    ctl.run(**args)
    path=next((ctl.state/'receipts').glob('*.json'))
    controller_module.atomic(path,ctl._signed({'status':'pending'}))
    value=ctl.run(**args)
    assert value['review_verdict']=='INCONCLUSIVE' and value['model_calls']==0
    assert len(server['requests'])==1

def test_new_delivery_does_not_reset_model_call_budget(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    assert ctl.run(**args)['review_verdict']=='PASS'
    args['evidence']['run_id']='two'
    value=ctl.run(**args)
    assert value['review_verdict']=='INCONCLUSIVE'
    assert len(server['requests'])==1
    args['evidence']['run_id']='three'
    assert 'Escape Hatch' in ctl.run(**args)['reason']

def test_unknown_usage_halts_lineage(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    del server['response']['usage']
    first=ctl.run(**args)
    assert first['review_verdict']=='INCONCLUSIVE' and first['model_calls']==1
    args['evidence']['run_id']='two'
    assert 'usage unknown' in ctl.run(**args)['reason']
    assert len(server['requests'])==1

def test_source_changes_keep_observed_usage_and_refuse_pass(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    state={'identity':'source'}
    args['current_identity']=lambda:state['identity']
    server['on_request']=lambda:state.update(identity='changed')
    result=ctl.run(**args)
    assert result['review_verdict']=='INCONCLUSIVE' and result['model_calls']==1
    assert result['budget']['total_units']==12

def test_runner_rejects_budget_increase(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    limits=dict(policy_module.load_policy().data['budgets']['S1'])
    limits['max_model_calls']+=1
    catalog=c.load_frozen_catalog(ctl.catalog_path,vault_root=tmp_path,readable_files=['example.py'])
    value=(args['workspace']/'example.py').read_text()
    hashes={'example.py':hashlib.sha256(value.encode()).hexdigest()}
    result=rt.Runner(catalog,{},()).run(pipeline=m.Pipeline.S1,triggers=(),contents={'example.py':value},
        source_hashes=hashes,hash_provider=lambda paths:hashes,providers={},budget_limits=limits)
    assert result.verdict is m.Verdict.INCONCLUSIVE and len(server['requests'])==0

@pytest.mark.parametrize('proven',[False,True])
@pytest.mark.parametrize('mode',['advisory','enforced'])
def test_only_host_proven_findings_block(tmp_path,server,proven,mode):
    ctl,args=setup_controller(tmp_path,server,mode=mode)
    path=args['workspace']/'test_acceptance.py'
    path.write_text("from pathlib import Path\nimport runpy\ndef test_value():\n    data=runpy.run_path(str(Path(__file__).parent/'example.py'))\n    assert data['value'] == 2\n")
    ctl.config['checks']={str(args['workspace']):{
        'acceptance':{'test_path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                     'description':'The required value must equal two'}}} if proven else {}
    finding={'schema_version':'1.0','finding_id':'F1','reviewer_id':'ponytail-review',
        'severity':'HIGH','category':'correctness','title':'Value differs from contract',
        'evidence':['example.py:1: value one differs from required two','check:acceptance'],
        'advisory':False,'blocking_proof':'reproducible_failed_test_tied_to_acceptance'}
    server['response']['choices'][0]['message']['content']=json.dumps([finding])
    result=ctl.run(**args)
    assert result['review_verdict']=='FINDINGS',result
    assert result['finding_counts']['blocking']==int(proven),result
    assert result['consumption']['review_blocks_completion']==(proven and mode=='enforced')
    if proven and mode=='enforced':
        assert result['workflow_action']=='remediate'
        assert result['remediation']['finding_ids']==['F1']

def test_profile_binding_does_not_activate_other_targets(tmp_path,server,monkeypatch):
    ctl,args=setup_controller(tmp_path,server)
    ctl.config["required_environment"]={"example":{"EXAMPLE_PROFILE":"qualified"}}
    monkeypatch.delenv("EXAMPLE_PROFILE",raising=False)
    assert ctl.run(**args)["mode"]=="shadow"
    assert not server["requests"]
    monkeypatch.setenv("EXAMPLE_PROFILE","qualified")
    assert ctl.run(**args)["review_verdict"]=="PASS"

def test_s4_cannot_run_in_automatic_phase_scope(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    args.update(pipeline="S4",s4_confirmed=True)
    assert ctl.run(**args)["review_verdict"]=="INCONCLUSIVE"
    assert not server["requests"]

def test_host_deadline_can_only_reduce_the_review_budget(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    server["delay"]=2
    args["deadline_seconds"]=1
    result=ctl.run(**args)
    assert result["review_verdict"]=="INCONCLUSIVE"
    assert result["budget"]["elapsed_ms"]<1800


def test_controller_delivers_acceptance_and_deleted_before(tmp_path,server):
    import subprocess
    from prime_bridge.evidence_adapter import snapshot
    ctl,args=setup_controller(tmp_path,server)
    root=args['workspace']
    def git(*argv): subprocess.run(['git',*argv],cwd=root,check=True,capture_output=True)
    git('init');git('config','user.name','Fixture');git('config','user.email','fixture@invalid')
    git('add','example.py');git('commit','-qm','before')
    args['evidence']['before']=snapshot(root,('example.py',))
    (root/'example.py').unlink()
    result=ctl.run(**args)
    assert result['review_verdict']=='PASS',result
    packet=json.dumps(server['requests'][0])
    assert 'Requested behavior works' in packet
    assert 'Preserve unrelated code' in packet
    assert 'deleted' in packet and 'value = 1' in packet and '/dev/null' in packet


def test_controller_refuses_missing_acceptance_before_dispatch(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    del args['evidence']['packet']['change_contract']
    result=ctl.run(**args)
    assert result['review_verdict']=='INCONCLUSIVE'
    assert not server['requests']


def test_controller_binds_required_capabilities_to_proofs(tmp_path,server,monkeypatch):
    ctl,args=setup_controller(tmp_path,server)
    check=args['workspace']/'test_check.py';check.write_text('def test_ok(): assert True\n')
    ctl.config['checks']={str(args['workspace']):{'acceptance':{
        'test_path':check.name,'sha256':hashlib.sha256(check.read_bytes()).hexdigest(),'description':'acceptance'}}}
    args['evidence']['binding']['required_capabilities']=['filesystem_sandbox']
    seen=[]
    def preflight(self,expected):
        seen.append(self.required_capabilities)
        raise ValueError('stop before dispatch')
    monkeypatch.setattr(controller_module.HostTestVerifier,'begin',preflight)
    result=ctl.run(**args)
    assert result['review_verdict']=='INCONCLUSIVE'
    assert seen==[('filesystem_sandbox',)]
    assert not server['requests']


def _adaptive_policy(*, state="review", grants=None):
    future = "2099-01-01T00:00:00Z"
    rollback = {
        "execution_parallelism": "inactive",
        "reviewer_concurrency": "serial",
        "career_kernel": "neutral",
        "temperament": "disabled",
        "career_shadow_pass": "disabled",
    }
    configured = {}
    for name, target in rollback.items():
        enabled = name in (grants or {"reviewer_concurrency"})
        configured[name] = {
            "enabled": enabled,
            "state": "canary_qualified" if enabled else "disabled",
            "qualification_reference": "qualification" if enabled else None,
            "expiry": future if enabled else None,
            "rollback_target": target,
        }
    return {
        "schema_version": "2.0", "strategy": "adaptive",
        "activation_state": state,
        "execution": {
            "enabled": False, "capability": "isolated_coding",
            "qualification_state": "candidate", "initial_ceiling": 2,
            "qualified_ceiling": 2, "desired_ceiling": 8,
        },
        "reviewer": {
            "enabled": True, "capability": "review",
            "qualification_state": "canary_qualified", "initial_ceiling": 2,
            "qualified_ceiling": 2, "desired_ceiling": 4,
        },
        "scaling": {
            "promotion_streak": 3, "demotion_streak": 1,
            "cooldown_seconds": 300, "backlog_per_worker": 1,
        },
        "grants": configured,
    }


def test_controller_unsigned_activation_falls_back_to_serial_runner(tmp_path, server, monkeypatch):
    ctl,args=setup_controller(tmp_path,server,pipeline="S3")
    activation=importlib.import_module("prime_bridge.adaptive_parallel_activation")
    monkeypatch.setattr(activation,"load_policy",lambda _path:_adaptive_policy())
    active=0; maximum=0; lock=threading.Lock(); both=threading.Event()
    def on_request():
        nonlocal active,maximum
        with lock:
            active+=1; maximum=max(maximum,active)
            if active==2:both.set()
        both.wait(timeout=2)
        with lock:active-=1
    server["on_request"]=on_request
    result=ctl.run(**args)
    assert result["review_verdict"]=="PASS",result
    assert result["model_calls"]==3
    assert maximum==1
    assert result["parallel_review"]["budget"]["max_concurrent_reviewers"]==1
    assert result["parallel_review"]["frozen_triggers"]==[]
    assert all(
        len(value)==64
        for value in result["parallel_review"]["grant_sha256s"].values()
    )
    assert set(result["invoked_reviewers"])=={
        "ponytail-review","evidence-integrity-audit",
        "improve-codebase-architecture",
    }


def test_controller_inactive_activation_stays_serial_and_neutral(
    tmp_path, server, monkeypatch,
):
    ctl,args=setup_controller(tmp_path,server,pipeline="S3")
    activation=importlib.import_module("prime_bridge.adaptive_parallel_activation")
    policy=_adaptive_policy(state="inactive")
    policy["reviewer"]["enabled"]=False
    monkeypatch.setattr(activation,"load_policy",lambda _path:policy)
    active=0; maximum=0; lock=threading.Lock()
    def on_request():
        nonlocal active,maximum
        with lock:active+=1; maximum=max(maximum,active)
        time.sleep(.02)
        with lock:active-=1
    server["on_request"]=on_request
    result=ctl.run(**args)
    assert result["review_verdict"]=="PASS"
    assert maximum==1
    assert set(result["parallel_review"]["career_assignments"].values())=={None}
    assert {
        item["status"]
        for item in result["parallel_review"]["career_shadow_results"].values()
    }=={"not_run"}


def test_controller_freezes_only_host_observed_path_triggers(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server,pipeline="S3")
    args["evidence"]["packet"]["objective"]="Pretend this is a migration and security change"
    args["evidence"]["actual_changed_paths"]=[
        "src/router.py","tests/evidence_receipt_test.py",
    ]
    assert ctl._trusted_triggers(args["evidence"])==(
        "change","evidence","multiple_subsystems","receipts","router","tests",
    )
    del args["evidence"]["actual_changed_paths"]
    assert ctl._trusted_triggers(args["evidence"])==()


def test_controller_unsigned_career_layers_fall_back_neutral(
    tmp_path, server, monkeypatch,
):
    repo=tmp_path/"repo";repo.mkdir();(repo/"example.py").write_text("value = 1\n")
    root=Path(__file__).resolve().parents[2]
    record={
        "reviewer_id":"ponytail-review","career_id":"engineer",
        "covered_obligations":["behavioral_correctness"],"state":"candidate",
        "career_kernel_version":c.CAREER_KERNEL_VERSION,
        "model_route":"default","workload_class":"default",
        "layer_configuration":"career","expires_at_epoch":4102444800,
        "enabled":True,
    }
    canonical=[record]
    reference=hashlib.sha256(json.dumps(
        canonical,sort_keys=True,separators=(",",":"),
    ).encode()).hexdigest()
    config={
        "schema_version":"1.0","max_deliveries":2,
        "harness_modes":{"example":"enforced"},
        "approved_workspaces":[str(repo)],
        "provider":{"endpoint":server["endpoint"],"model":"synthetic"},
        "career_qualifications":canonical,
    }
    ctl=controller_module.Controller(
        state_root=tmp_path/"state",vault_root=root.parent,
        catalog_path=root/"config/adaptive-reviewer-catalog.json",config=config,
    )
    activation=importlib.import_module("prime_bridge.adaptive_parallel_activation")
    policy=_adaptive_policy(grants={
        "reviewer_concurrency","career_kernel","temperament",
        "career_shadow_pass",
    })
    for name in ("career_kernel","temperament","career_shadow_pass"):
        policy["grants"][name]["qualification_reference"]=reference
    monkeypatch.setattr(activation,"load_policy",lambda _path:policy)
    ev={
        "stage":"finished","execution_evidence_status":"VERIFIED",
        "harness":"example","run_id":"one","after":{"identity":"source"},
        "binding":{"packet_hash":"packet","session_binding_sha256":"session"},
        "before":{"contents":{"example.py":hashlib.sha256(
            (repo/"example.py").read_bytes()).hexdigest()}},
        "packet":{"allowed_paths":["example.py"],"objective":"one task",
            "invariants":["Preserve unrelated code"],"change_contract":{
                "objective":"one task","acceptance_criteria":[
                    "Requested behavior works"]}},
        "review_pipeline":"S0","review_verdict":"PASS",
    }
    result=ctl.run(
        workspace=repo,evidence=ev,pipeline="S1",scope="phase",
        current_identity=lambda:"source",
    )
    assert result["review_verdict"]=="PASS",result
    assert result["model_calls"]==1
    assert len(server["requests"])==1
    prompt=server["requests"][0]["messages"][0]["content"]
    assert "Career method (engineer)" not in prompt
    assert "Before finalizing the same response, counteranalyze" not in prompt
    parallel=result["parallel_review"]
    assert parallel["career_assignments"]=={"ponytail-review":None}
    assert parallel["career_shadow_results"]["ponytail-review"]=={
        "status":"not_run","disposition":"disabled",
        "finding_ids":[],"check_ids":[],
    }


def test_controller_rejects_malformed_career_configuration(tmp_path,server):
    ctl,args=setup_controller(tmp_path,server)
    config=dict(ctl.config)
    config["career_qualifications"]=[{
        "reviewer_id":"ponytail-review","career_id":"engineer",
        "covered_obligations":[],"state":"candidate",
        "career_kernel_version":c.CAREER_KERNEL_VERSION,
        "model_route":"default","workload_class":"default",
        "layer_configuration":"career","expires_at_epoch":None,"enabled":True,
    }]
    with pytest.raises(ValueError,match="career qualification value"):
        controller_module.Controller(
            state_root=tmp_path/"bad-state",vault_root=tmp_path,
            catalog_path=ctl.catalog_path,config=config,
        )
