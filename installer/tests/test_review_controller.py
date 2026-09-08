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
        'packet':{'allowed_paths':['example.py'],'objective':'one task'},'review_pipeline':'S0','review_verdict':'PASS'}
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
