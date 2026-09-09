"""Shared bounded completion controller. Call only after trusted host validation."""
from contextlib import contextmanager
import hashlib, hmac, json, os
from pathlib import Path
import tempfile, math
from .catalog import load_frozen_catalog
from .adapters import ReportOnlyProvider
from .models import Pipeline, Verdict
from .policy import load_policy
from .proofs import HostCheck, HostTestVerifier
from .runner import Runner
from .transport import JsonReviewTransport

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".review-")
    try:
        with os.fdopen(fd,"w") as file:json.dump(value,file,sort_keys=True,indent=2)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

@contextmanager
def lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    file=path.open("a+b")
    try:
        if os.name=="nt":
            import msvcrt
            file.seek(0, 2)
            if file.tell() == 0: file.write(b"0"); file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:file.close()

class Controller:
    def __init__(self, *, state_root, vault_root, catalog_path, config, approval_ledger=None):
        self.approval_ledger = approval_ledger
        self.state=Path(state_root)
        self.vault=Path(vault_root)
        self.catalog_path=Path(catalog_path)
        self.config=dict(config)
        if set(config)-{"schema_version","provider","harness_modes","approved_workspaces","checks","max_deliveries","required_environment","automatic_s4","budget_exceptions_enabled"}:
            raise ValueError("unknown completion configuration field")
        if type(config.get("budget_exceptions_enabled", False)) is not bool:
            raise ValueError("budget exception activation must be boolean")
        if not isinstance(config.get("harness_modes",{}),dict):raise ValueError("harness modes must be a mapping")
        if config.get("schema_version")!="1.0" or type(config.get("max_deliveries")) is not int or not 1<=config["max_deliveries"]<=2:
            raise ValueError("invalid completion limits")
        automatic=config.get("automatic_s4", {})
        if not isinstance(automatic, dict) or set(automatic)-{"enabled_harnesses", "goal_states"}:
            raise ValueError("invalid automatic S4 configuration")
        enabled=automatic.get("enabled_harnesses", [])
        goals=automatic.get("goal_states", {})
        if (not isinstance(enabled, list) or any(not isinstance(h, str) or h not in config.get("harness_modes", {}) for h in enabled)
                or len(enabled) != len(set(enabled)) or not isinstance(goals, dict)
                or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k,v in goals.items())):
            raise ValueError("invalid automatic S4 harness or goal binding")
        environments=config.get("required_environment",{})
        if not isinstance(environments,dict) or any(not isinstance(v,dict) or any(
            not isinstance(k,str) or not k.isidentifier() or not isinstance(value,str)
            for k,value in v.items()) for v in environments.values()):
            raise ValueError("invalid native environment binding")
        if any(mode not in {"shadow","advisory","enforced"} for mode in config.get("harness_modes",{}).values()):
            raise ValueError("invalid harness review mode")

    def mode(self,harness):
        bindings=self.config.get("required_environment",{}).get(harness,{})
        if any(os.environ.get(key)!=value for key,value in bindings.items()):return "shadow"
        return self.config.get("harness_modes",{}).get(harness,"shadow")

    def refusal(self,reason,pipeline,harness):
        mode=self.mode(harness)
        return {"schema_version":"review-completion.v1","review_pipeline":pipeline,
            "review_verdict":"INCONCLUSIVE","review_completed":False,"reviewer_invoked":False,
            "invoked_reviewers":[],"findings":[],"finding_counts":{"total":0,"blocking":0},
            "model_calls":0,"budget":None,"reason":reason,"mode":mode,
            "consumption":{"review_passed":False,"review_blocks_completion":mode=="enforced","advisory":mode!="enforced"},
            "workflow_action":"hold" if mode=="enforced" else "advisory","cache_hit":False}

    def _key(self):
        self.state.mkdir(parents=True,exist_ok=True)
        path=self.state/"receipt-signing.key"
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"wb") as file:file.write(os.urandom(32))
        except FileExistsError:pass
        key=path.read_bytes()
        if len(key)!=32:raise ValueError("receipt key unavailable")
        return key

    def _signed(self,value):
        return {"data":value,"signature":hmac.new(self._key(),json.dumps(value,sort_keys=True,separators=(",",":")).encode(),hashlib.sha256).hexdigest()}

    def _read(self,path):
        if path.stat().st_size>2_000_000:raise ValueError("oversized completion receipt")
        envelope=json.loads(path.read_text())
        expected=self._signed(envelope["data"])["signature"]
        if not hmac.compare_digest(envelope["signature"],expected):raise ValueError("altered completion receipt")
        return envelope["data"]

    def prepare_budget_exception(self, **request):
        """Trusted host only: draft a proposal; never writes approval or invokes a model."""
        from .budget_exception import prepare
        return prepare(self, **request)

    def _authority_fingerprint(self):
        return digest({"policy":load_policy().fingerprint, "configuration":self.config,
            "catalog":hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(),
            "implementation":{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in Path(__file__).parent.glob("*.py")}})

    def _resolution_binding(self, workspace, evidence, scope):
        return {"workspace":str(Path(workspace).resolve()), "harness":evidence["harness"],
                "session":evidence["binding"].get("session_binding_sha256"),
                "task":evidence["packet"]["objective"], "packet":evidence["binding"]["packet_hash"],
                "authorization":digest(evidence["binding"].get("authorization")),
                "paths":sorted(evidence["packet"]["allowed_paths"]), "scope":scope}

    def _lineage(self, workspace, evidence):
        return digest({"workspace":str(Path(workspace).resolve()), "harness":evidence["harness"],
                       "session":evidence["binding"].get("session_binding_sha256"),
                       "task":evidence["packet"]["objective"]})

    def run(self, **request):
        result = self._run(**request)
        if result["consumption"]["review_blocks_completion"]:
            # Only this existing controller seals recovery evidence. Callers cannot
            # supply a PASS, a resolution status, or a replacement signing key.
            review = self._read(Path(result["receipt_path"])) if result.get("receipt_path") else result
            binding = self._resolution_binding(request["workspace"], request["evidence"], request["scope"])
            block = {"binding":binding, "run_id":request["evidence"]["run_id"],
                     "source_identity":request["evidence"]["after"]["identity"],
                     "lineage":self._lineage(request["workspace"], request["evidence"]), "review":review}
            block_id = digest(block)
            path = self.state/"blocks"/(block_id+".json")
            if not path.exists():atomic(path, self._signed(block))
            else:
                if self._read(path) != block:raise ValueError("altered review block")
            result = dict(result, block_id=block_id)
        return result

    def _block(self, block_id):
        if not isinstance(block_id, str) or len(block_id)!=64 or any(c not in "0123456789abcdef" for c in block_id):
            raise ValueError("invalid block identity")
        value = self._read(self.state/"blocks"/(block_id+".json"))
        if digest(value) != block_id:raise ValueError("altered block identity")
        review = value["review"]
        if review.get("receipt_path") and self._read(Path(review["receipt_path"])) != review:
            raise ValueError("original signed review changed")
        return value

    def _recoverable(self, block, workspace, evidence, scope, pipeline, current_identity):
        if self._resolution_binding(workspace,evidence,scope) != block["binding"]:
            raise ValueError("resolution task/session/workspace/harness/scope mismatch")
        if not block["binding"]["session"]:
            raise ValueError("resolution requires native session binding")
        if evidence.get("stage")!="finished" or evidence.get("execution_evidence_status")!="VERIFIED":
            raise ValueError("verified fresh execution required")
        if current_identity()!=evidence["after"]["identity"]:
            raise ValueError("resolution source is stale")
        if int(Pipeline(pipeline).value[1:]) < int(block["review"]["review_pipeline"][1:]):
            raise ValueError("resolution review depth is lower")
        path=self.state/"lineages"/(block["lineage"]+".json")
        ledger=self._read(path) if path.exists() else {}
        if ledger.get("pending_retest"):raise ValueError("interrupted retest hold cannot recover")
        if ledger.get("unknown_usage"):
            raise ValueError("unknown usage hold cannot recover")
        return ledger

    def recover_refusal(self, *, block_id, **request):
        block=self._block(block_id)
        self._recoverable(block, **{k:request[k] for k in
            ("workspace","evidence","scope","pipeline","current_identity")})
        review=block["review"]
        # Closed list of pre-dispatch failures. Budget, interrupted delivery,
        # unsettled usage, runner failures, and arbitrary reasons never retry.
        if (review["reason"] not in {"workspace is not admitted for this provider",
                                    "review provider configuration unavailable"}
                or review.get("model_calls") != 0 or review.get("receipt_path")
                or review.get("budget") is not None
                or request["evidence"]["run_id"] != block["run_id"]
                or request["evidence"]["after"]["identity"] != block["source_identity"]):
            raise ValueError("block is not a known pre-dispatch zero-usage refusal")
        return self.run(**request)

    def _retest_block(self, block, workspace, evidence, replacement, ledger, current_identity):
        """Re-run original pinned checks under the existing host executor and ledger."""
        from .proofs import PROOF
        from .transport import ReviewBudgetTracker
        from .adapters import _safe_path
        workspace=Path(workspace).resolve()
        findings=[f for f in block['review'].get('findings',[]) if f.get('blocking_proof')]
        if len(findings)!=block['review']['finding_counts']['blocking']:
            raise ValueError('original blocking evidence incomplete')
        registered=self.config.get('checks',{}).get(str(workspace),{})
        checks={}; minimum={}
        for finding in findings:
            refs=[v[6:] for v in finding.get('evidence',[]) if isinstance(v,str) and v.startswith('check:')]
            if finding.get('blocking_proof')!=PROOF or len(refs)!=1 or refs[0] not in registered:
                raise ValueError('original blocking check unavailable')
            key=refs[0]; check=HostCheck(**registered[key])
            proofs=[p for p in block['review'].get('proof_receipts',[]) if p.get('check_id')==key
                and p.get('verified') is True and p.get('test_sha256')==check.sha256
                and type(p.get('tests')) is int and p['tests']>0]
            if not proofs:raise ValueError('original authenticated check proof unavailable')
            checks[key]=check;minimum[key]=max(p['tests'] for p in proofs)
        limits=dict(load_policy().data['budgets'][replacement['review_pipeline']])
        for key in limits:limits[key]=min(limits[key],ledger['limits'][key])
        for key,field in {'max_tool_calls':'tool_calls','max_elapsed_seconds':'elapsed_seconds'}.items():
            limits[key]=max(0,limits[key]-ledger['spent'].get(field,0))
        paths=evidence['packet']['allowed_paths']
        if len(paths)>limits['max_files']:raise ValueError('retest file budget exceeded')
        hashes={};presence={};size=0
        for rel in paths:
            target=(workspace/_safe_path(rel)).resolve()
            if not target.is_relative_to(workspace):raise ValueError('retest scope escape')
            presence[rel]=target.exists()
            if presence[rel]:
                size+=target.stat().st_size
                if size>limits['max_context_bytes']:raise ValueError('retest context budget exceeded')
            hashes[rel]=hashlib.sha256(target.read_bytes() if presence[rel] else b'').hexdigest()
        verifier=HostTestVerifier(workspace,checks,hashes,
            required_capabilities=evidence['binding'].get('required_capabilities', ()),
            source_presence=presence)
        verifier.preflight(hashes)
        budget=ReviewBudgetTracker(limits);budget.remaining()
        if limits['max_tool_calls']<len(checks):raise ValueError('retest tool budget exhausted')
        if current_identity()!=evidence['after']['identity']:raise ValueError('retest source is stale')
        ledger_path=self.state/'lineages'/(block['lineage']+'.json')
        # A process interruption leaves an explicit hold, never reusable unaccounted work.
        ledger['pending_retest']=block['review']['receipt_path']
        atomic(ledger_path,self._signed(ledger))
        settled=False
        try:
            for finding in findings:verifier.verify(finding,budget)
            passed={p['check_id']:p for p in verifier.receipts if p.get('passed') is True}
            if any(k not in passed or passed[k]['tests']<minimum[k] for k in checks):
                raise ValueError('original blocking check did not pass retest')
            if current_identity()!=evidence['after']['identity']:raise ValueError('retest source changed')
            settled=True
            return verifier.receipts
        except Exception:
            settled=True
            raise
        finally:
            if settled:
                ledger['spent']['tool_calls']=ledger['spent'].get('tool_calls',0)+budget.tool_calls
                ledger['spent']['elapsed_seconds']=ledger['spent'].get('elapsed_seconds',0)+math.ceil(budget.public()['elapsed_ms']/1000)
                ledger['last_retest']={'block_id':digest(block),'receipts':verifier.receipts}
                ledger.pop('pending_retest',None)
                atomic(ledger_path,self._signed(ledger))

    def resolve_block(self, *, block_id, workspace, evidence, scope, current_identity):
        """Trusted host API: evidence must already be freshly host-revalidated.

        Reads both signed reviews itself; preserves receipts and lineage spend.
        Returns an owner-signed, exact-block resolution, never a caller status.
        """
        block=self._block(block_id)
        delivery={"workspace":str(Path(workspace).resolve()),"harness":evidence["harness"],"run_id":evidence["run_id"]}
        replacement=self._read(self.state/"receipts"/(digest(delivery)+".json"))
        with lock(self.state/"locks"/(block["lineage"]+".lock")):
            ledger=self._recoverable(block,workspace,evidence,scope,replacement["review_pipeline"],current_identity)
            original=block["review"]
            if (replacement.get("authority_fingerprint") != self._authority_fingerprint()
                    or replacement.get("delivery") != delivery or replacement.get("resolution_binding") != block["binding"]
                    or replacement.get("review_verdict")!="PASS" or replacement.get("review_completed") is not True
                    or replacement.get("mode")!="enforced" or replacement["consumption"]["review_blocks_completion"]
                    or replacement.get("source_identity")!=evidence["after"]["identity"]
                    or replacement.get("lineage")!=block["lineage"]):
                raise ValueError("replacement is not an authenticated matching successful review")
            if original.get("finding_counts",{}).get("blocking",0):
                if (evidence["run_id"]==block["run_id"] or evidence["after"]["identity"]==block["source_identity"]
                        or replacement.get("review_sequence",0)<=original.get("review_sequence",0)
                        or ledger.get("deliveries",0)<replacement["review_sequence"]):
                    raise ValueError("fresh same-lineage remediation required")
            elif (original["reason"] not in {"workspace is not admitted for this provider",
                                            "review provider configuration unavailable"}
                    or original.get("model_calls")!=0 or original.get("receipt_path")
                    or evidence["run_id"]!=block["run_id"]):
                raise ValueError("usage or budget hold cannot resolve")
            resolution_path=self.state/"resolutions"/(block_id+".json")
            if resolution_path.exists():
                prior=self._read(resolution_path)
                if (prior.get('replacement_receipt_sha256')==digest(replacement)
                        and prior.get('source_identity')==evidence['after']['identity']
                        and (not original.get('finding_counts',{}).get('blocking',0) or prior.get('retest_receipts'))):
                    return prior
                raise ValueError('resolution identity changed')
            retests=self._retest_block(block,workspace,evidence,replacement,ledger,current_identity) if original.get('finding_counts',{}).get('blocking',0) else []
            resolution={"block_id":block_id,"binding":block["binding"],"retest_receipts":retests,
                "original_run_id":block["run_id"],"replacement_run_id":evidence["run_id"],
                "finding_ids":[f["finding_id"] for f in original.get("findings",[]) if f.get("blocking_proof")],
                "replacement_receipt_sha256":digest(replacement),"source_identity":evidence["after"]["identity"],
                "lineage":block["lineage"],"cumulative_ledger_sha256":digest(ledger)}
            atomic(self.state/"resolutions"/(block_id+".json"),self._signed(resolution))
            return resolution

    def _run(self, *, workspace, evidence, pipeline, scope, current_identity, s4_confirmed=False, deadline_seconds=None, budget_exception_id=None, s4_grant=None):
        """evidence is a host-validated finished receipt, never caller-reported test status."""
        if type(s4_confirmed) is not bool:raise ValueError("S4 confirmation must be boolean")
        from .models import Scope
        scope = Scope(scope).value
        if deadline_seconds is not None and (type(deadline_seconds) is not int or deadline_seconds < 1):
            raise ValueError("invalid host deadline")
        workspace=Path(workspace).resolve()
        result = None
        dispatched = False
        harness=evidence["harness"];pipeline=Pipeline(pipeline)
        refuse=lambda why:self.refusal(why,pipeline.value,harness)
        if budget_exception_id is not None and self.config.get("budget_exceptions_enabled", False) is not True:
            return refuse("budget exceptions are disabled")
        if budget_exception_id is not None and pipeline not in {Pipeline.S1, Pipeline.S2, Pipeline.S3}:
            return refuse("budget exception cannot alter S0 or S4 admission")
        if evidence.get("stage")!="finished" or evidence.get("execution_evidence_status")!="VERIFIED":
            return refuse("host execution evidence is not verified")
        if current_identity()!=evidence["after"]["identity"]:
            return refuse("host source identity is stale")
        automatic = False
        if s4_grant is not None:
            from .policy import validate_s4_grant
            try:
                automatic = pipeline is Pipeline.S4 and validate_s4_grant(
                    s4_grant, owner=self, workspace=workspace, evidence=evidence, scope=scope)
            except (ValueError, OSError):return refuse("invalid S4 policy authorization")
            if not automatic:return refuse("S4 grant cannot authorize another pipeline")
        if pipeline is Pipeline.S4 and not (s4_confirmed or automatic):return refuse("S4 confirmation required")
        mode=self.mode(harness)
        if pipeline.value not in load_policy().data["scopes"][scope]["allowed"] and not automatic:
            return refuse("review depth exceeds this scope; S4 requires explicit system or plan scope")
        if pipeline is not Pipeline.S0:
            if mode=="shadow":return refuse("semantic invocation remains shadow for this harness")
            approved=[Path(p).resolve() for p in self.config.get("approved_workspaces",[])]
            if not any(workspace.is_relative_to(p) for p in approved):
                return refuse("workspace is not admitted for this provider")
        packet=evidence["packet"]; paths=tuple(packet["allowed_paths"])
        delivery={"workspace":str(workspace),"harness":harness,"run_id":evidence["run_id"]}
        file=self.state/"receipts"/(digest(delivery)+".json")
        authority_fingerprint = self._authority_fingerprint()
        fingerprint=digest({"delivery":delivery,"source":evidence["after"]["identity"],
            "packet":evidence["binding"]["packet_hash"],"policy":load_policy().fingerprint,
            "configuration":self.config,"pipeline":pipeline.value,
            "catalog":hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(),
            "implementation":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}})
        lineage=digest({"workspace":str(workspace),"harness":harness,
            "session":evidence["binding"].get("session_binding_sha256"),
            "task":packet["objective"]})
        if budget_exception_id is not None:
            fingerprint=digest({"completion":fingerprint,"budget_exception_id":budget_exception_id})
        try:
            with lock(self.state/"locks"/(lineage+".lock")):
                if file.exists():
                    old=self._read(file)
                    if old.get("status") == "pending":return refuse("prior delivery interrupted; review usage is unknown; no automatic retry")
                    if old["fingerprint"]!=fingerprint:return refuse("delivery identity changed; start a fresh routed delivery")
                    if current_identity()!=evidence["after"]["identity"]:return refuse("cached source is stale")
                    return dict(old,cache_hit=True,model_calls=0)
                if pipeline is Pipeline.S0:
                    if evidence.get("review_pipeline")!="S0" or evidence.get("review_verdict")!="PASS":
                        return refuse("S0 host PASS is not proven")
                    result=None; findings=[]; verdict="PASS"; used=0; budget=None; invoked=[]; proofs=[]
                else:
                    ledger_path=self.state/"lineages"/(lineage+".json")
                    ledger=self._read(ledger_path) if ledger_path.exists() else {"deliveries":0,"limits":load_policy().data["budgets"][pipeline.value],"spent":{}}
                    if ledger["deliveries"]>=self.config["max_deliveries"]:
                        return refuse("Loop Limit Reached - Escape Hatch")
                    if ledger.get("pending_retest"):return refuse("interrupted retest hold; automatic reuse forbidden")
                    if ledger.get("unknown_usage"):return refuse("prior review usage unknown; automatic budget reuse forbidden")
                    limits = dict(load_policy().data["budgets"][pipeline.value])
                    for key in limits: limits[key] = min(limits[key], ledger["limits"][key])
                    if deadline_seconds is not None:
                        limits["max_elapsed_seconds"] = min(limits["max_elapsed_seconds"], deadline_seconds)
                    cumulative = {"max_model_calls":"model_requests","max_estimated_input_tokens":"estimated_input_units",
                                  "max_output_tokens":"output_units","max_total_tokens":"total_units",
                                  "max_tool_calls":"tool_calls","max_elapsed_seconds":"elapsed_seconds"}
                    for key, field in cumulative.items():
                        limits[key] = max(0, limits[key] - ledger["spent"].get(field, 0))
                    grant = None
                    if budget_exception_id is not None:
                        from .budget_exception import consume
                        grant = consume(self, budget_exception_id, workspace=workspace, evidence=evidence,
                            pipeline=pipeline, scope=scope, current_identity=current_identity)
                        limits = dict(grant.limits)
                        if deadline_seconds is not None:
                            limits["max_elapsed_seconds"] = min(limits["max_elapsed_seconds"], deadline_seconds)
                    if len(paths) > limits["max_files"]:return refuse("review scope exceeds file budget")
                    from .context import review_material, material_hashes
                    contents, source_hashes, review_context = review_material(
                        workspace, evidence, limits["max_context_bytes"])
                    def hashes(requested):
                        return material_hashes(
                            workspace, review_context["changes"], requested,
                            limits["max_context_bytes"])
                    if current_identity()!=evidence["after"]["identity"]:return refuse("source changed during packet construction")
                    catalog=load_frozen_catalog(self.catalog_path,vault_root=self.vault,readable_files=paths)
                    provider=self.config.get("provider",{})
                    if set(provider)-{"endpoint","model","credential_env","reasoning_effort"}:return refuse("unknown provider field")
                    transport=JsonReviewTransport(**provider)
                    try:transport.validate()
                    except ValueError:return refuse("review provider configuration unavailable")
                    providers={key:ReportOnlyProvider(transport=transport) for key in catalog.reviewers}
                    registered=self.config.get("checks",{}).get(str(workspace),{})
                    verifier=HostTestVerifier(
                        workspace, {k:HostCheck(**v) for k,v in registered.items()}, source_hashes,
                        required_capabilities=evidence["binding"].get("required_capabilities", ()),
                        source_presence={
                            path: value["after_sha256"] != "ABSENT"
                            for path, value in review_context["changes"].items()
                        }) if registered else None
                    # Pre-dispatch file/configuration errors spent no model usage.
                    # Claim immediately before invocation; interrupted requests stay closed.
                    atomic(file,self._signed({"status":"pending","fingerprint":fingerprint}))
                    ledger["deliveries"]+=1
                    ledger["unknown_usage"]=True
                    atomic(ledger_path,self._signed(ledger))
                    dispatched = True
                    result=Runner(catalog,{},tuple(load_policy().data["blocking_proof_forms"])).run(
                        pipeline=pipeline,triggers=(),contents=contents,source_hashes=source_hashes,
                        hash_provider=hashes,providers=providers,s4_confirmed=s4_confirmed,s4_grant=s4_grant,
                        proof_verifier=verifier,legacy_authoritative=mode!="enforced",adaptive_authoritative=mode=="enforced",budget_limits=limits,
                        budget_grant=grant,review_context=review_context)
                    verdict=result.verdict.value
                    findings=list(result.reconciliation.findings) if result.reconciliation else []
                    invoked=list(result.invoked_reviewers);budget=result.budget
                    used=budget["model_requests"] if budget else 0
                    proofs=list(result.proof_receipts)
                    ledger["unknown_usage"] = bool(budget and not budget["usage_complete"])
                    if budget:
                        for field in cumulative.values():
                            amount = math.ceil(budget["elapsed_ms"]/1000) if field == "elapsed_seconds" else budget.get(field)
                            if field=="estimated_input_units" and amount is not None:
                                amount=max(amount,budget.get("input_units") or 0)
                            if amount is not None:ledger["spent"][field]=ledger["spent"].get(field,0)+amount
                    atomic(ledger_path,self._signed(ledger))
                if current_identity()!=evidence["after"]["identity"]:
                    verdict="INCONCLUSIVE"
                    findings=[]
                blocking=sum(bool(f.get("blocking_proof")) for f in findings)
                complete=verdict in {"PASS","FINDINGS"}
                blocks=mode=="enforced" and (not complete or blocking>0)
                value={"schema_version":"review-completion.v1","fingerprint":fingerprint,
                    "authority_fingerprint":authority_fingerprint,
                    "resolution_binding":self._resolution_binding(workspace,evidence,scope),
                    "lineage":lineage,"review_sequence":ledger["deliveries"] if pipeline is not Pipeline.S0 else 0,
                    "scope":scope,"declared_paths":list(paths),"delivery":delivery,"source_identity":evidence["after"]["identity"],
                    "review_pipeline":pipeline.value,"review_verdict":verdict,"review_completed":complete,
                    "reviewer_invoked":bool(invoked),"invoked_reviewers":invoked,"findings":findings,
                    "finding_counts":{"total":len(findings),"blocking":blocking},
                    "model_calls":used,"budget":budget,"proof_receipts":proofs,
                    "receipt_path":str(file),"mode":mode,"cache_hit":False,"budget_exception_id":budget_exception_id,
                    "workflow_action":"remediate" if blocks and blocking else "hold" if blocks else "continue",
                    "consumption":{"review_passed":verdict=="PASS","review_blocks_completion":blocks,"advisory":mode!="enforced"},
                    "reason":"review completed" if complete else "review incomplete",
                    "controls":list(result.controls) if result else [],
                    "remediation": {
                        "route":"scripts/prime-code-execute.py",
                        "finding_ids":[f["finding_id"] for f in findings if f.get("blocking_proof")],
                        "declared_paths":list(paths),
                        "instruction":"Correct only independently verified findings, then deliver fresh execution evidence within the remaining review budget."
                    } if blocks and blocking else None}
                atomic(file,self._signed(value))
                return value
        except (OSError,ValueError,KeyError,TypeError,RuntimeError):
            value=refuse("review controller unavailable, busy, or invalid")
            if dispatched:
                value["model_calls"] = result.budget["model_requests"] if result and result.budget else None
                value["budget"] = result.budget if result else None
                value["reviewer_invoked"] = bool(result.invoked_reviewers) if result else None
            return value
