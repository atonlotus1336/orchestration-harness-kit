"""Host-owned bridge: verified execution evidence -> one shared completion receipt."""
from pathlib import Path
import json
import os
import subprocess
import sqlite3

from prime_bridge.evidence_adapter import EvidenceAdapter, snapshot
from prime_bridge.omp_task_packet import OmpTaskPacket
from .context import ReviewFact, review_material
from .controller import Controller
from .models import Pipeline, Scope
from .policy import load_policy
from .router import select_review, scope_review_facts

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/adaptive-review-runtime.json"

class ExistingJobReader:
    """Inspect an existing governed owner without creating or migrating a ledger."""
    def __init__(self,path):
        self.path=Path(path).resolve(strict=True)
        if not self.path.is_file():raise ValueError("existing job ledger required")
    def get(self,job_id):
        try:
            with sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True) as db:
                row=db.execute("SELECT record_json FROM jobs WHERE job_id=?",(job_id,)).fetchone()
        except sqlite3.Error as exc:
            raise ValueError("existing job ledger is not readable") from exc
        if not row or len(row[0])>2_000_000:raise ValueError("governed job missing or oversized")
        return json.loads(row[0])


def controller(*, config_path=None, state_root=None, approval_ledger=None):
    path = Path(config_path or os.environ.get("LAPART_REVIEW_CONFIG", DEFAULT_CONFIG))
    config = json.loads(path.read_text()) if path.exists() else {
        "schema_version":"1.0", "max_deliveries":2, "harness_modes":{}, "approved_workspaces":[]
    }
    # Settings are host configuration. No provider/model is chosen by reviewers.
    return Controller(state_root=state_root or Path(os.environ.get(
        "LAPART_REVIEW_STATE", str(Path.home()/".lapart/adaptive-review"))),
        vault_root=ROOT.parent, catalog_path=ROOT/"config/adaptive-reviewer-catalog.json", config=config,
        approval_ledger=approval_ledger)

def complete(*, workspace, receipt_path, expected_run_id=None, expected_harness=None,
             paths=None, scope="phase", required_level="S0", facts=(), s4_confirmed=False,
             config_path=None, state_root=None, policy_path=None, deadline_seconds=None, job_ledger_path=None, job_id=None, allow_automatic_s4=False,
             budget_exception_id=None, budget_proposal=None, capability_ledger_path=None, recovery_block_id=None):
    from .budget_approval import ExistingApprovalReader
    approval_ledger = ExistingApprovalReader(capability_ledger_path) if capability_ledger_path is not None else None
    owner = controller(config_path=config_path, state_root=state_root, approval_ledger=approval_ledger)
    adapter=EvidenceAdapter(Path(workspace),review_policy=policy_path)
    evidence=adapter.read(Path(receipt_path))
    harness = evidence["harness"]
    def refuse(reason): return owner.refusal(reason, required_level, harness)
    policy = load_policy(policy_path) if policy_path else load_policy()
    if policy.fingerprint != load_policy().fingerprint:
        return refuse("completion policy differs from qualified runner policy")
    if evidence.get("stage") != "finished":
        return refuse("finished host evidence required")
    if (job_ledger_path is None)!=(job_id is None):return refuse("existing job ledger and job id must be supplied together")
    ledger=ExistingJobReader(job_ledger_path) if job_ledger_path is not None else None
    # Finished-only revalidation: checks the original lifecycle owner, packet
    # binding and current snapshot without rerunning tests or claiming a job.
    evidence=adapter.finish(Path(receipt_path),job_ledger=ledger,job_id=job_id)
    if expected_run_id is not None and expected_run_id != evidence["run_id"]:
        return refuse("evidence run identity mismatch")
    if expected_harness is not None and expected_harness != harness:
        return refuse("evidence harness identity mismatch")
    packet = OmpTaskPacket.from_mapping(evidence["packet"])
    if paths is not None and tuple(sorted(paths)) != tuple(sorted(packet.allowed_paths)):
        return refuse("evidence declared scope mismatch")
    current = snapshot(Path(workspace),packet.allowed_paths)["identity"]
    if current != evidence["after"]["identity"]:return refuse("host source identity is stale")
    changed=tuple(evidence.get("actual_changed_paths",packet.allowed_paths))
    changed_lines=None
    try:
        limit=max(value["max_context_bytes"] for value in policy.data["budgets"].values())
        _contents,_hashes,material=review_material(Path(workspace),evidence,limit)
        if set(changed)==set(material["changes"]):
            changed_lines=sum(1 for change in material["changes"].values()
                for line in change["diff"].splitlines()
                if line.startswith(("+","-")) and not line.startswith(("+++","---")))
    except (OSError,UnicodeError,ValueError):
        # Unknown stays conservative. The Controller repeats exact admission
        # and returns an explicit refusal before any reviewer request.
        pass
    subsystems=len({p.split("/")[0] if "/" in p else "." for p in changed})
    observed = list(scope_review_facts(changed,policy,changed_lines=changed_lines,subsystem_count=subsystems))
    if changed_lines is None:
        observed.append(ReviewFact("binary_scope", "S1",load_bearing=True,known=False))
    # The deterministic owner may establish exact S0 eligibility, but structural
    # facts and caller-requested deeper review can only raise that lower bound.
    host_level = "S0" if evidence.get("review_pipeline") == "S0" and evidence.get("review_verdict") == "PASS" else "S1"
    owned_level = evidence.get("review_pipeline")
    if owned_level not in {level.value for level in Pipeline}:
        return refuse("invalid host review minimum")
    host_level = max(host_level, owned_level, key=lambda level: int(level[1:]))
    if packet.risk_tier == "high":host_level = max(host_level, "S3")
    elif packet.risk_tier != "low" and host_level == "S0":host_level = "S1"
    observed.extend((ReviewFact("verified_host_minimum", host_level),ReviewFact("requested_minimum",required_level)))
    observed.extend(facts)
    admission = None
    if allow_automatic_s4 and recovery_block_id is None and scope == "phase" and not s4_confirmed:
        from .s4_dispatch import candidate, dispatch
        admission = candidate(owner, workspace=workspace, evidence=evidence, observed=observed)
        if admission.get("scenarios") and admission["status"] in {"DEFERRED", "CHECKPOINT"}:
            result = owner.refusal("; ".join(admission["reasons"]), "S4", harness)
            result["automatic_s4_candidate"] = admission
            return result
        if admission["status"] == "CANDIDATE":
            if budget_exception_id is not None or budget_proposal is not None:
                return owner.refusal("budget exception cannot override automatic S4 admission", "S4", harness)
            def run_automatic(grant):
                result = owner.run(workspace=workspace,evidence=evidence,pipeline=Pipeline.S4,
                    scope=scope,current_identity=lambda:snapshot(Path(workspace),packet.allowed_paths)["identity"],
                    s4_grant=grant,deadline_seconds=deadline_seconds)
                from .event_hooks import evaluate_event_hooks
                try:
                    result["evaluation_hooks"] = evaluate_event_hooks(
                        pipeline=Pipeline.S4,declared_paths=packet.allowed_paths,
                        actual_changed_paths=evidence.get("actual_changed_paths",()),
                        evidence_status="VERIFIED",before_identity=evidence["before"]["identity"],
                        after_identity=evidence["after"]["identity"],
                        current_identity=snapshot(Path(workspace),packet.allowed_paths)["identity"],
                        findings=result.get("findings",()),s4_grant=grant,policy=policy.data)
                except (ValueError,OSError):
                    # Return the already-accounted review to dispatch. Do not
                    # lose known usage when an authorization becomes stale.
                    result.update(review_completed=False,review_verdict='INCONCLUSIVE',workflow_action='hold')
                    result['consumption'].update(review_passed=False,review_blocks_completion=True)
                    result['reason']='evaluation authorization changed'
                return result
            result = dispatch(owner, admission=admission, workspace=workspace, evidence=evidence,
                current_identity=lambda:snapshot(Path(workspace),packet.allowed_paths)["identity"],
                run=run_automatic)
            result["evidence_provenance"] = "host_verified"
            result["execution_evidence_status"] = evidence["execution_evidence_status"]
            result["evidence_receipt_path"] = str(receipt_path)
            result["verified_run_id"] = evidence["run_id"]
            result["routing_reasons"] = list(admission["scenarios"])
            return result
    decision = select_review(observed, policy, Scope(scope), s4_confirmed=s4_confirmed)
    if not decision.runnable:
        value = owner.refusal("; ".join(decision.exclusions),decision.pipeline.value,harness)
        value["requires_confirmation"] = decision.requires_confirmation
        return value
    if budget_proposal is not None:
        if budget_exception_id is not None or set(budget_proposal) != {"limits", "expires_at"}:
            return refuse("closed, draft-only budget proposal required")
        try:
            proposal = owner.prepare_budget_exception(workspace=workspace, evidence=evidence,
                pipeline=decision.pipeline, scope=scope,
                current_identity=lambda:snapshot(Path(workspace),packet.allowed_paths)["identity"],
                deadline_seconds=deadline_seconds, **budget_proposal)
        except (ValueError, OSError, KeyError) as exc:
            return refuse(str(exc))
        result = owner.refusal("budget proposal prepared; approval and activation still required", decision.pipeline.value, harness)
        result["budget_proposal"] = proposal
        result["evidence_provenance"] = "host_verified"
        result["verified_run_id"] = evidence["run_id"]
        return result
    run = owner.run if recovery_block_id is None else lambda **kw: owner.recover_refusal(block_id=recovery_block_id, **kw)
    result = run(workspace=workspace,evidence=evidence,pipeline=decision.pipeline,
        scope=scope,current_identity=lambda:snapshot(Path(workspace),packet.allowed_paths)["identity"],
        s4_confirmed=s4_confirmed,deadline_seconds=deadline_seconds,budget_exception_id=budget_exception_id)
    if admission is not None:
        result["automatic_s4_candidate"] = admission
    result["evidence_provenance"] = "host_verified"
    result["execution_evidence_status"] = evidence.get("execution_evidence_status")
    result["evidence_receipt_path"] = str(receipt_path)
    result["verified_run_id"] = evidence["run_id"]
    result["routing_reasons"] = list(decision.reasons)
    from .event_hooks import evaluate_event_hooks
    result["evaluation_hooks"] = evaluate_event_hooks(
        pipeline=decision.pipeline,declared_paths=packet.allowed_paths,
        actual_changed_paths=evidence.get("actual_changed_paths",()),
        evidence_status="VERIFIED",before_identity=evidence["before"]["identity"],
        after_identity=evidence["after"]["identity"],current_identity=current,
        findings=result.get("findings",()),s4_confirmed=s4_confirmed,policy=policy.data)
    return result


def resolve_completion_block(*, block_id, workspace, original_run_id, harness, session_binding,
                             replacement_receipt_path, retry_refusal=False,
                             config_path=None, state_root=None, job_ledger_path=None, job_id=None):
    """Explicit existing-owner API; never accepts review results or status flags.

    Finding resolutions require an already finished fresh routed execution and
    its signed successful review. Only a known zero-usage refusal can dispatch
    here, after the normal host and controller revalidation.
    """
    owner=controller(config_path=config_path,state_root=state_root)
    block=owner._block(block_id)
    if (block["run_id"]!=original_run_id or block["binding"]["harness"]!=harness
            or block["binding"]["session"]!=session_binding
            or block["binding"]["workspace"]!=str(Path(workspace).resolve())):
        raise ValueError("original blocked delivery mismatch")
    adapter=EvidenceAdapter(Path(workspace))
    evidence=adapter.read(Path(replacement_receipt_path))
    if evidence.get("stage")!="finished":raise ValueError("finished fresh execution required")
    ledger=ExistingJobReader(job_ledger_path) if job_ledger_path is not None else None
    evidence=adapter.finish(Path(replacement_receipt_path),job_ledger=ledger,job_id=job_id)
    if retry_refusal:
        review=complete(workspace=workspace,receipt_path=replacement_receipt_path,
            expected_run_id=original_run_id,expected_harness=harness,scope=block["binding"]["scope"],
            required_level=block["review"]["review_pipeline"],config_path=config_path,state_root=state_root,
            job_ledger_path=job_ledger_path,job_id=job_id,recovery_block_id=block_id)
        if review.get("review_verdict")!="PASS":raise ValueError("review still blocks completion")
    return owner.resolve_block(block_id=block_id,workspace=workspace,evidence=evidence,
        scope=block["binding"]["scope"],current_identity=lambda:snapshot(Path(workspace),tuple(evidence["packet"]["allowed_paths"]))["identity"])
