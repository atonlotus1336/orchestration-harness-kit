"""Host-owned conditional S4 admission and one source-bound execution claim."""
from pathlib import Path
import hashlib
import json
from .controller import atomic, digest, lock
from .policy import load_policy
from .s4_scenarios import recommend_s4


def candidate(owner, *, workspace, evidence, observed, explicit_action="none"):
    config = owner.config.get("automatic_s4", {})
    harness = evidence["harness"]
    if (not isinstance(config, dict) or set(config) - {"enabled_harnesses", "goal_states"}
            or harness not in config.get("enabled_harnesses", [])
            or owner.mode(harness) != "enforced"):
        return {"status": "DISABLED", "automatic_start_authorized": False, "model_calls": 0}
    workspace = Path(workspace).resolve()
    packet = evidence["packet"]
    paths = list(evidence.get("actual_changed_paths", packet["allowed_paths"]))
    structural = sorted({f.provenance for f in observed if f.key == "structural_path"})
    subsystems = max([int(f.provenance.split(":")[-1]) for f in observed
                      if f.key == "subsystem_count"] or [0])
    objective = packet["objective"]
    task_contract = digest({"workspace": str(workspace), "objective": objective,
                            "paths": sorted(packet["allowed_paths"])})
    goal_id, contract_hash = "task:" + task_contract, task_contract
    goal_open, event, system_gate, work_due, weakness = True, "checkpoint", False, False, False
    loop_limit = False
    # The runtime config names an exact project-local goal state, never a
    # path supplied by a reviewer. Native goals remain owned by their harness.
    rel = config.get("goal_states", {}).get(str(workspace))
    goal_binding = None
    if rel:
        from .. import goalposts
        path = goalposts.local_file(workspace, rel)
        if path.stat().st_size > 1_000_000:
            raise ValueError("oversized goal state")
        raw = path.read_bytes()
        state = json.loads(raw)
        state_status = goalposts.status(state)
        current = goalposts.fingerprint(workspace, state["contract"]["inputs"])
        latest = state["history"][-1] if state["history"] else None
        if not latest or latest["input_fingerprint"] != current:
            return {"status": "DEFERRED", "reasons": ["fresh-goalpost-evidence-required"], "model_calls": 0}
        goal_id, contract_hash = state["contract"]["goal_id"], state["contract_hash"]
        goal_open = state["status"] == "active"
        loop_limit = state_status["decision"] == "checkpoint"
        event = latest.get("trigger", "checkpoint")
        if event not in {"milestone", "pre_completion", "active_work_goalpost"}:
            event = "checkpoint"
        system_gate = any(c["id"] == "system_review" for c in state["contract"]["endpoint"])
        work_due = event == "active_work_goalpost"
        # Only accepted host proof references can establish a weakness; prose
        # or a goal category score does not turn into an independent finding.
        weakness = any(f.get("blocking_proof") for f in evidence.get("contract_findings", []))
        goal_binding = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
        explicit_action = state.get("s4_action", explicit_action)
    policy = load_policy().data
    lineage = digest({"workspace": str(workspace), "harness": harness,
                      "session": evidence["binding"].get("session_binding_sha256"),
                      "task": objective})
    ledger_path = owner.state / "lineages" / (lineage + ".json")
    remaining = dict(policy["budgets"]["S4"])
    known = True
    if ledger_path.exists():
        ledger = owner._read(ledger_path)
        known = not ledger.get("unknown_usage", False)
        loop_limit |= ledger["deliveries"] >= owner.config["max_deliveries"]
        fields = {"max_model_calls": "model_requests", "max_estimated_input_tokens": "estimated_input_units",
                  "max_output_tokens": "output_units", "max_total_tokens": "total_units",
                  "max_tool_calls": "tool_calls", "max_elapsed_seconds": "elapsed_seconds"}
        for key in remaining:
            remaining[key] = min(remaining[key], ledger["limits"][key])
            if key in fields:
                remaining[key] = max(0, remaining[key] - ledger.get("spent", {}).get(fields[key], 0))
    facts = {
        "endpoint_fixed": bool(objective.strip()), "goal_open": goal_open,
        "safe_boundary": evidence.get("stage") == "finished",
        "source_verified": evidence.get("execution_evidence_status") == "VERIFIED",
        "system_gate_required": system_gate, "active_work_due": work_due,
        "cross_system_weakness": weakness, "loop_limit_reached": loop_limit,
        "same_source_pending": False, "budget_known": known, "event": event,
        "goal_id": goal_id, "contract_hash": contract_hash,
        "source_identity": evidence["after"]["identity"], "previous_s4_source": None,
        "explicit_action": explicit_action, "changed_paths": paths,
        "structural_paths": structural, "subsystem_count": subsystems,
        "remaining_budget": remaining,
    }
    dedup = hashlib.sha256(json.dumps([goal_id, contract_hash, evidence["after"]["identity"]],
                                     separators=(",", ":")).encode()).hexdigest()
    claim_key = digest({"workspace": str(workspace), "candidate": dedup})
    claim = owner.state / "automatic-s4" / (claim_key + ".json")
    if claim.exists():
        # Reuse/refuse under dispatch's lock; do not request another allowance.
        # Refusals such as an explicit deferral or a closed goal still win.
        facts["remaining_budget"] = dict(policy["budgets"]["S4"])
        facts["budget_known"] = True
        facts["loop_limit_reached"] = False
    result = recommend_s4(facts, policy)
    result["goal_binding"] = goal_binding
    result["budget_reservation"] = dict(policy["budgets"]["S4"])
    return result


def dispatch(owner, *, admission, workspace, evidence, run, current_identity):
    """Persist a claim before any model call. Interrupted/unknown use cannot replay."""
    if admission.get("status") != "CANDIDATE":
        return owner.refusal("automatic S4 has no admitted scenario", "S4", evidence["harness"])
    key = digest({"workspace": str(Path(workspace).resolve()),
                  "candidate": admission["deduplication_key"]})
    path = owner.state / "automatic-s4" / (key + ".json")
    def fresh():
        if current_identity() != evidence["after"]["identity"]:
            raise ValueError("automatic S4 source changed")
        goal = admission.get("goal_binding")
        if goal and hashlib.sha256(Path(goal["path"]).read_bytes()).hexdigest() != goal["sha256"]:
            raise ValueError("automatic S4 goal changed")
    result = None
    started = False
    qualification = digest({"policy": load_policy().fingerprint, "config": owner.config,
                            "code": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in Path(__file__).parent.glob("*.py")}})
    try:
        with lock(owner.state / "automatic-s4-locks" / (key + ".lock")):
            fresh()
            if path.exists():
                record = owner._read(path)
                if record.get("qualification") != qualification:
                    return owner.refusal("automatic S4 qualification changed; inspect prior receipt", "S4", evidence["harness"])
                if record["status"] != "completed":
                    return owner.refusal("automatic S4 interrupted or pending; no automatic replay", "S4", evidence["harness"])
                return dict(record["result"], model_calls=0, cache_hit=True)
            atomic(path, owner._signed({"status": "pending", "admission": admission, "qualification": qualification}))
            # Existing Controller/Runner own all model, tool, token and deadline
            # ceilings. This claim never creates a second allowance or retry.
            started = True
            from .policy import issue_s4_grant
            grant = issue_s4_grant(owner, workspace=workspace, evidence=evidence, admission=admission)
            result = run(grant)
            fresh()
            result["s4_authorization"] = "automatic-policy"
            result["s4_scenarios"] = admission["scenarios"]
            if result.get("review_completed"):
                atomic(path, owner._signed({"status": "completed", "result": result, "qualification": qualification}))
            return result
    except (OSError, ValueError, KeyError, RuntimeError):
        failure = owner.refusal("automatic S4 claim busy, stale or invalid", "S4", evidence["harness"])
        if started:
            failure["model_calls"] = result.get("model_calls") if result else None
            failure["budget"] = result.get("budget") if result else None
        return failure
