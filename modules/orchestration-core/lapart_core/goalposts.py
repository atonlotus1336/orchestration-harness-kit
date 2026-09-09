"""Evidence-backed goalpost decisions for existing goal loops; no model calls.

This module evaluates and records decisions. It does not dispatch workers,
change Codex native goals, authorize side effects, or schedule background work.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import time
from typing import Any


class GoalpostError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _number(value: Any, name: str, minimum: float = 0) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise GoalpostError(f"{name} must be a finite number >= {minimum}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoalpostError(f"{name} is required")
    return value.strip()


def local_file(root: Path, name: str) -> Path:
    path = PurePosixPath(_text(name, "relative path").replace("\\", "/"))
    if path.is_absolute() or PureWindowsPath(name).drive or ".." in path.parts:
        raise GoalpostError("paths must stay inside the project")
    result = (root / str(path)).resolve()
    if not result.is_relative_to(root.resolve()):
        raise GoalpostError("symlink escapes project")
    return result


def fingerprint(root: Path, inputs: list[str]) -> str:
    """Fingerprint explicit files only; missing planned files count as missing."""
    values = {}
    for name in sorted(inputs):
        path = local_file(root, name)
        if path.is_dir():
            raise GoalpostError("inputs must name files, not directories")
        values[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return digest(values)


def validate_contract(raw: dict) -> dict:
    contract = deepcopy(raw)
    for field in ("goal_id", "objective", "builder_id"):
        contract[field] = _text(contract.get(field), field)
    if not isinstance(contract.get("inputs"), list) or not contract["inputs"]:
        raise GoalpostError("explicit input files are required")
    if len(set(contract["inputs"])) != len(contract["inputs"]):
        raise GoalpostError("duplicate input paths")
    endpoint = contract.get("endpoint")
    if not isinstance(endpoint, list) or not endpoint:
        raise GoalpostError("a finite, verifiable endpoint is required before work starts")
    ids = set()
    for check in endpoint:
        ident = _text(check.get("id"), "endpoint id")
        _text(check.get("description"), "endpoint description")
        if ident in ids:
            raise GoalpostError("duplicate endpoint id")
        ids.add(ident)
    categories = contract.get("categories")
    if not isinstance(categories, list) or len(categories) < 2:
        raise GoalpostError("at least two quality categories are required")
    category_ids = set()
    for category in categories:
        ident = _text(category.get("id"), "category id")
        _text(category.get("label"), "category label")
        if ident in category_ids:
            raise GoalpostError("duplicate category id")
        category_ids.add(ident)
        category.setdefault("threshold", 80)
        threshold = _number(category["threshold"], "threshold", 1)
        if threshold > 100 or (ident == "usability" and threshold < 80):
            raise GoalpostError("threshold must be <=100; usability minimum is 80")
        if not category.get("checks"):
            raise GoalpostError("every category needs a fixed rubric")
        judgment = False
        for check in category["checks"]:
            check_id = _text(check.get("id"), "check id")
            _text(check.get("label"), "check label")
            if check_id in ids:
                raise GoalpostError("check ids must be globally unique")
            ids.add(check_id)
            check.setdefault("weight", 1)
            _number(check["weight"], "check weight", 0.001)
            if check.get("kind") not in ("deterministic", "judgment"):
                raise GoalpostError("check kind must be deterministic or judgment")
            judgment |= check["kind"] == "judgment"
            check.setdefault("critical", False)
            if type(check["critical"]) is not bool:
                raise GoalpostError("critical must be boolean")
        if ident == "usability" and not judgment:
            raise GoalpostError("usability requires independent human or reviewer judgment")
    if "usability" not in category_ids:
        raise GoalpostError("human usability is mandatory, including CLI/docs outputs")
    for key, default in (("cadence_seconds", 1800), ("max_goalposts", 6),
                         ("no_progress_limit", 2), ("max_wall_seconds", 14400)):
        contract.setdefault(key, default)
        if type(contract[key]) is not int or contract[key] < 1:
            raise GoalpostError(f"{key} must be a positive integer")
    protected = contract.setdefault("protected_scores", {})
    for key, score in protected.items():
        if key not in category_ids or _number(score, "protected score") > 100:
            raise GoalpostError("invalid protected score")
    return contract


def create(raw: dict, *, now: float | None = None) -> dict:
    contract = validate_contract(raw)
    return {"version": 1, "contract": contract, "contract_hash": digest(contract),
            "created_at": time.time() if now is None else now, "status": "active",
            "active_seconds": 0, "evaluated_seconds": 0, "work_events": {},
            "history": [], "floors": dict(contract["protected_scores"]),
            "passed_checks": [], "passed_endpoint": [], "stagnant_rounds": 0}


def _validate_state(state: dict) -> None:
    if state.get("version") != 1 or digest(state["contract"]) != state.get("contract_hash"):
        raise GoalpostError("goal contract changed; create an explicitly revised goal")
    validate_contract(state["contract"])


def status(state: dict, *, now: float | None = None, milestone: bool = False,
           completion: bool = False) -> dict:
    _validate_state(state)
    now = time.time() if now is None else now
    contract = state["contract"]
    reasons = []
    if state["status"] != "active":
        return {"decision": state["status"], "due": False, "reasons": ["run is closed; explicit revision/resumption required"]}
    if now - state["created_at"] >= contract["max_wall_seconds"]:
        return {"decision": "checkpoint", "due": True, "reasons": ["wall-clock ceiling reached"]}
    if state.get("pending_evaluation"):
        reasons.append(state["pending_evaluation"])
    if not state["history"]:
        reasons.append("baseline required")
    if state["active_seconds"] - state["evaluated_seconds"] >= contract["cadence_seconds"]:
        reasons.append("active-work interval elapsed")
    if milestone:
        reasons.append("milestone")
    if completion:
        reasons.append("completion requested")
    return {"decision": "evaluate" if reasons else "continue", "due": bool(reasons), "reasons": reasons}


def record_work(state: dict, event_id: str, active_seconds: float) -> dict:
    _validate_state(state)
    event_id = _text(event_id, "work event id")
    _number(active_seconds, "active work seconds")
    if event_id in state["work_events"]:
        if state["work_events"][event_id] != active_seconds:
            raise GoalpostError("work event replay has a different duration")
        return state
    if state["status"] != "active":
        raise GoalpostError("cannot record work on a closed goal")
    state["work_events"][event_id] = active_seconds
    state["active_seconds"] += active_seconds
    return state


def _evidence_owner():
    """Resolve the existing host receipt owner; never create a signing identity."""
    from .review.host import controller
    owner = controller()
    return owner if (owner.state / "receipt-signing.key").is_file() else None


def _proof(root: Path, evidence: dict | None, current: str, *, judgment: bool,
           builder: str, contract_hash: str, check_id: str, owner) -> str:
    if not isinstance(evidence, dict) or evidence.get("input_fingerprint") != current:
        return "unknown"
    if type(evidence.get("passed")) is not bool:
        return "unknown"
    if judgment and (evidence.get("method") not in ("human", "independent_review")
                     or not evidence.get("assessor_id") or evidence["assessor_id"] == builder):
        return "unknown"
    if not judgment and evidence.get("method") not in ("deterministic", "human", "independent_review"):
        return "unknown"
    try:
        path = local_file(root, evidence.get("artifact", ""))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != evidence.get("sha256"):
            return "unknown"
        # The artifact hash protects bytes, not the claims inside them. Only
        # the existing host owner may attest a verdict and assessor identity.
        from .review.controller import Controller
        if type(owner) is not Controller or not (owner.state / "receipt-signing.key").is_file():
            return "unknown"
        receipt = owner._read(path)
        if (receipt.get("schema_version") != "goal-assessment.v1"
                or receipt.get("contract_hash") != contract_hash
                or receipt.get("input_fingerprint") != current):
            return "unknown"
        claim = receipt.get("checks", {}).get(check_id)
        if not isinstance(claim, dict) or any(claim.get(k) != evidence.get(k)
                for k in ("passed", "method", "assessor_id")):
            return "unknown"
        if type(claim.get("passed")) is not bool or not claim.get("assessor_id"):
            return "unknown"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return "unknown"
    return "pass" if claim["passed"] else "fail"


def evaluate(state: dict, observations: dict, root: Path, *, now: float | None = None,
             milestone: bool = False, completion: bool = False, evidence_owner=None) -> dict:
    _validate_state(state)
    now = time.time() if now is None else now
    if state["status"] != "active":
        raise GoalpostError("goal is closed; do not silently restart or widen it")
    contract = state["contract"]
    current = fingerprint(root, contract["inputs"])
    valid_ids = {c["id"] for c in contract["endpoint"]} | {
        check["id"] for c in contract["categories"] for check in c["checks"]}
    if not isinstance(observations, dict) or set(observations) - valid_ids:
        raise GoalpostError("unknown check ids; rubric cannot grow during a run")
    if evidence_owner is None:
        try:
            evidence_owner = _evidence_owner()
        except (ImportError, OSError, ValueError, KeyError, TypeError):
            evidence_owner = None
    artifacts = {}
    for ident, observation in observations.items():
        try:
            path = local_file(root, observation.get("artifact", ""))
            artifacts[ident] = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, GoalpostError, AttributeError):
            artifacts[ident] = None
    evidence_id = digest({"inputs": current, "artifacts": artifacts, "observations": observations,
                          "active_seconds": state["active_seconds"], "completion": completion, "milestone": milestone})
    proof = lambda ident, judgment=False: _proof(root, observations.get(ident), current,
                         judgment=judgment, builder=contract["builder_id"],
                         contract_hash=state["contract_hash"], check_id=ident, owner=evidence_owner)
    authenticated = {ident: proof(ident, any(c["id"] == ident and c["kind"] == "judgment"
        for category in contract["categories"] for c in category["checks"])) for ident in valid_ids}
    evidence_id = digest({"evidence": evidence_id, "authenticated": authenticated})
    if (now - state["created_at"] < contract["max_wall_seconds"]
            and state["history"] and state["history"][-1]["evidence_id"] == evidence_id):
        return state["history"][-1]
    proof = lambda ident, judgment=False: authenticated[ident]
    endpoint = {item["id"]: proof(item["id"]) for item in contract["endpoint"]}
    categories, weaknesses, passed_checks = {}, [], set()
    regression = False
    for category in contract["categories"]:
        ident = category["id"]
        checks = {c["id"]: proof(c["id"], c["kind"] == "judgment") for c in category["checks"]}
        total = sum(c["weight"] for c in category["checks"])
        score = round(100 * sum(c["weight"] for c in category["checks"] if checks[c["id"]] == "pass") / total, 2)
        floor = state["floors"].get(ident, 0)
        lost = sorted(set(state["passed_checks"]) & {k for k,v in checks.items() if v != "pass"})
        critical = any(c["critical"] and checks[c["id"]] != "pass" for c in category["checks"])
        unknown = any(v == "unknown" for v in checks.values())
        regressed = score < floor or bool(lost)
        regression |= regressed
        accepted = score >= max(category["threshold"], floor) and not (critical or unknown or regressed)
        categories[ident] = {"label": category["label"], "score": score,
            "threshold": category["threshold"], "floor": floor, "passed": accepted,
            "regression": regressed, "lost_checks": lost, "checks": checks}
        passed_checks.update(k for k,v in checks.items() if v == "pass")
        if not accepted:
            weaknesses.append({"category": ident, "label": category["label"],
                "check_labels": [c["label"] for c in category["checks"] if checks[c["id"]] != "pass"], "priority": 0 if ident == "usability" or regressed or critical else 1,
                "gap": max(0, max(category["threshold"], floor) - score),
                "checks": [k for k,v in checks.items() if v != "pass"],
                "action": "collect current evidence" if unknown else "repair failed checks"})
    passed_endpoint = {k for k,v in endpoint.items() if v == "pass"}
    endpoint_regression = bool(set(state["passed_endpoint"]) - passed_endpoint)
    regression |= endpoint_regression
    missing_endpoint = [k for k,v in endpoint.items() if v != "pass"]
    if missing_endpoint:
        weaknesses.append({"category": "endpoint", "label": "Goal endpoint",
                           "check_labels": [c["description"] for c in contract["endpoint"] if c["id"] in missing_endpoint], "priority": 0 if endpoint_regression else 1,
                           "gap": len(missing_endpoint), "checks": missing_endpoint,
                           "action": "complete the fixed acceptance criteria"})
    weaknesses.sort(key=lambda item: (item["priority"], -item["gap"], item["category"]))
    progress = bool((passed_checks - set(state["passed_checks"])) or
                    (passed_endpoint - set(state["passed_endpoint"]))) and not regression
    previous = state["history"]
    stagnant = 0 if progress or not previous else state["stagnant_rounds"] + 1
    round_count = len(previous) + 1
    all_pass = not missing_endpoint and all(v["passed"] for v in categories.values())
    stop_reason = None
    if now - state["created_at"] >= contract["max_wall_seconds"]:
        stop_reason = "wall-clock ceiling reached"
    elif round_count >= contract["max_goalposts"]:
        stop_reason = "goalpost limit reached"
    elif stagnant >= contract["no_progress_limit"]:
        stop_reason = "no measurable progress; endpoint may be infeasible"
    decision = "complete" if all_pass else "checkpoint" if stop_reason else "iterate"
    report = {"goal_id": contract["goal_id"], "round": round_count, "decision": decision,
        "trigger": "pre_completion" if completion else "milestone" if milestone else
            "active_work_goalpost" if state["active_seconds"] - state["evaluated_seconds"] >= contract["cadence_seconds"] else "checkpoint",
        "reason": "fixed endpoint and every category passed" if all_pass else stop_reason or "address the weakest evidence-backed categories",
        "input_fingerprint": current, "evidence_id": evidence_id,
        "active_seconds": state["active_seconds"], "endpoint": endpoint,
        "endpoint_labels": {c["id"]: c["description"] for c in contract["endpoint"]},
        "categories": categories, "weaknesses": weaknesses, "focus_next": weaknesses[:2],
        "stagnant_rounds": stagnant, "model_calls": 0}
    state.pop("pending_evaluation", None)
    state["history"].append(report)
    state["evaluated_seconds"] = state["active_seconds"]
    state["stagnant_rounds"] = stagnant
    # Preserve each authenticated gain even when another category regresses.
    # This does not count a mixed regression round as overall progress.
    state["passed_checks"] = sorted(set(state["passed_checks"]) | passed_checks)
    state["passed_endpoint"] = sorted(set(state["passed_endpoint"]) | passed_endpoint)
    for ident, category in categories.items():
        state["floors"][ident] = max(state["floors"].get(ident, 0), category["score"])
    if decision in ("complete", "checkpoint"):
        state["status"] = decision
    return report


def markdown(report: dict) -> str:
    lines = [f"# Goalpost {report['round']}: {report['decision'].capitalize()}", "", report["reason"], "",
             "| Area | Score | Required | Result |", "|---|---:|---:|---|"]
    for category in report["categories"].values():
        label = category["label"].replace("|", "\\|").replace("\n", " ")
        result = "Pass" if category["passed"] else "Regression" if category["regression"] else "Needs work"
        lines.append(f"| {label} | {category['score']:g}/100 | {max(category['threshold'], category['floor']):g} | {result} |")
    lines += ["", "Endpoint:"]
    for ident, outcome in report["endpoint"].items():
        text = {"pass":"Pass", "fail":"Needs work", "unknown":"Needs verification"}[outcome]
        lines.append(f"- {text}: {report['endpoint_labels'][ident]}")
    lines += ["", "Next focus:"]
    lines += [f"- {w['label']}: {w['action']} — {'; '.join(w['check_labels'])}." for w in report["focus_next"]]
    if not report["focus_next"]:
        lines.append("- Endpoint reached. Stop this goal; propose additional work separately.")
    return "\n".join(lines) + "\n"


@contextmanager
def locked_state(path: Path):
    """Single-writer guard. An interrupted writer requires explicit lock cleanup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise GoalpostError("goal state is locked by another writer; inspect before retrying") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def save(path: Path, state: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
