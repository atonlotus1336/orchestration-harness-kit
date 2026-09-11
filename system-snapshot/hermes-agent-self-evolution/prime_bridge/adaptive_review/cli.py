"""Manual adaptive-review interface backed by the root policy and receipt schema."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Sequence

from jsonschema import Draft7Validator, FormatChecker

from prime_bridge.omp_task_packet import OmpTaskPacket

from .context import DeterministicCheckResult, ReviewFact, evaluate_change_contract
from .event_hooks import evaluate_event_hooks
from .models import Pipeline, Scope, Verdict
from .notifications import CompletionNotification, NotificationState, S4Action, apply_s4_action, redact
from .policy import load_policy
from .router import select_review, scope_review_facts, structured_review_facts

REPO_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_SCHEMA = REPO_ROOT / "schemas" / "adaptive-code-review-receipt.schema.json"
EXIT_OK = 0
EXIT_INCONCLUSIVE = 1
EXIT_USAGE = 2


def _safe_declared_paths(values: Sequence[str]) -> tuple[str, ...]:
    result = []
    for value in values:
        path = PurePosixPath(value.replace("\\", "/"))
        if not value or path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
            raise ValueError(f"unsafe declared path: {value!r}")
        result.append(str(path))
    if not result:
        raise ValueError("at least one declared path is required")
    return tuple(sorted(set(result)))


def _deterministic_results(values: Sequence[str]) -> tuple[DeterministicCheckResult, ...]:
    results = []
    for raw in values:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) - {
            "command", "exit_status", "phase", "acceptance_criterion",
        }:
            raise ValueError("deterministic result must use the closed command/status/phase/criterion shape")
        if not isinstance(value.get("command"), str) or not value["command"].strip():
            raise ValueError("deterministic result command is required")
        if type(value.get("exit_status")) is not int:
            raise ValueError("deterministic result exit_status must be an integer")
        phase = value.get("phase", "post_change")
        criterion = value.get("acceptance_criterion")
        if not isinstance(phase, str) or not phase.strip():
            raise ValueError("deterministic result phase must be a non-empty string")
        if criterion is not None and (not isinstance(criterion, str) or not criterion.strip()):
            raise ValueError("deterministic result acceptance_criterion must be null or a non-empty string")
        results.append(DeterministicCheckResult(
            command=value["command"].strip(),
            exit_status=value["exit_status"],
            phase=phase.strip(),
            acceptance_criterion=criterion.strip() if criterion is not None else None,
        ))
    return tuple(results)


def _contract_evaluation(args: argparse.Namespace, paths: tuple[str, ...]):
    if not args.task_packet:
        return None
    raw = json.loads(Path(args.task_packet).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("task packet must be a JSON object")
    raw.pop("packet_hash", None)
    packet = OmpTaskPacket.from_mapping(raw)
    if packet.change_contract is None:
        raise ValueError("task packet does not contain a change contract")
    if packet.change_contract.declared_paths != paths:
        raise ValueError("review paths do not match the task packet change contract")
    actual_paths = _safe_declared_paths(args.actual_changed_path)
    return evaluate_change_contract(
        packet.change_contract,
        actual_changed_paths=actual_paths,
        deterministic_test_results=_deterministic_results(args.deterministic_test_result),
        static_analysis_results=_deterministic_results(args.static_analysis_result),
    )


def _decision(args: argparse.Namespace, paths: tuple[str, ...], evaluation=None):
    facts = [ReviewFact("requested", args.required_level, load_bearing=args.unknown_load_bearing, known=not args.unknown_load_bearing)]
    policy = load_policy(Path(args.policy))
    facts.extend(scope_review_facts(
        evaluation.actual_changed_paths if evaluation is not None else paths, policy,
        changed_lines=args.changed_lines, subsystem_count=args.subsystem_count,
    ))
    facts.extend(structured_review_facts(json.loads(args.routing_facts) if args.routing_facts else None, changed_lines=args.changed_lines, subsystem_count=args.subsystem_count))
    if evaluation is not None:
        facts.extend(evaluation.facts)
    return select_review(facts, policy, Scope(args.scope), s4_confirmed=args.s4_confirmed)


def _decision_json(decision, paths):
    return {
        "scope": decision.scope.value,
        "pipeline": decision.pipeline.value,
        "verdict": decision.verdict.value,
        "reasons": list(decision.reasons),
        "exclusions": list(decision.exclusions),
        "runnable": decision.runnable,
        "requires_confirmation": decision.requires_confirmation,
        "policy_fingerprint": decision.policy_fingerprint,
        "budget": decision.budget.__dict__,
        "declared_paths": list(paths),
        "reviewer_invoked": False,
        "classification_only": True,
        "review_completed": False,
    }


def _observe_git(workspace: Path = REPO_ROOT) -> tuple[dict, str]:
    from ..evidence_adapter import EvidenceError, _git
    try:
        status = _git(workspace, "status", "--porcelain").decode("utf-8")
        head = _git(workspace, "rev-parse", "--short", "HEAD").decode("ascii").strip()
    except (EvidenceError, UnicodeError):
        return {"observed": False, "dirty": None, "changed_count": None}, "unobserved"
    return {"observed": True, "dirty": bool(status.strip()), "changed_count": len(status.splitlines())}, (
        f"committed:{head}" if head else "unobserved")


def cmd_evaluate(args: argparse.Namespace) -> int:
    paths = _safe_declared_paths(args.path)
    evaluation = _contract_evaluation(args, paths)
    decision = _decision(args, paths, evaluation)
    output = _decision_json(decision, paths)
    if evaluation is not None:
        output["change_contract_evidence"] = evaluation.receipt_data()
        output["contract_findings"] = [item.canonical_data() for item in evaluation.findings]
        if evaluation.inconclusive:
            output["verdict"] = Verdict.INCONCLUSIVE.value
        elif evaluation.findings:
            output["verdict"] = Verdict.FINDINGS.value
    output["evaluation_hooks"] = evaluate_event_hooks(
        pipeline=decision.pipeline,
        declared_paths=paths,
        actual_changed_paths=evaluation.actual_changed_paths if evaluation is not None else (),
        evidence_status="UNVERIFIED",
        findings=(item.canonical_data() for item in evaluation.findings) if evaluation is not None else (),
        s4_confirmed=args.s4_confirmed,
        policy=load_policy(Path(args.policy)).data,
    )
    print(json.dumps(redact(output), sort_keys=True, indent=2))
    inconclusive = decision.verdict is Verdict.INCONCLUSIVE or bool(evaluation and evaluation.inconclusive)
    return EXIT_INCONCLUSIVE if inconclusive else EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    paths = _safe_declared_paths(args.path)
    evaluation = _contract_evaluation(args, paths)
    if args.s4_action == "Run now":args.s4_confirmed=True
    decision = _decision(args, paths, evaluation)
    if decision.pipeline is Pipeline.S4 and args.s4_action in {"Later","Skip"}:
        from .host import controller
        output=controller().refusal("S4 deferred by user" if args.s4_action=="Later" else "S4 waived by user","S4","unknown")
        output["state"]="deferred" if args.s4_action=="Later" else "waived"
        print(json.dumps(redact(output),sort_keys=True,indent=2))
        return EXIT_INCONCLUSIVE
    if args.evidence_receipt:
        from .host import complete, controller
        from .router import RISK_FACTS
        from .context import ReviewFact
        # Caller facts may request more scrutiny, never certify a pass.
        facts = list(scope_review_facts(paths,load_policy(Path(args.policy)),
                     changed_lines=args.changed_lines,subsystem_count=args.subsystem_count))
        observed=json.loads(args.routing_facts) if args.routing_facts else {}
        structured_review_facts(observed)  # validate the closed input shape
        facts.extend(ReviewFact(k,v) for k,v in RISK_FACTS.items() if observed.get(k) is True)
        if args.unknown_load_bearing:
            facts.append(ReviewFact("unknown",args.required_level,load_bearing=True,known=False))
        try:
            if not args.evidence_run_id:raise ValueError("evidence run id is required")
            if evaluation is not None and (evaluation.inconclusive or evaluation.findings):
                raise ValueError("caller-contract-findings-require-host-verification")
            if args.task_packet:
                from prime_bridge.evidence_adapter import EvidenceAdapter
                host=EvidenceAdapter(Path(args.workspace),review_policy=Path(args.policy)).read(Path(args.evidence_receipt))
                raw=json.loads(Path(args.task_packet).read_text());raw.pop("packet_hash",None)
                if OmpTaskPacket.from_mapping(raw).packet_hash != OmpTaskPacket.from_mapping(host["packet"]).packet_hash:
                    raise ValueError("task packet differs from host evidence")
            output=complete(workspace=Path(args.workspace),receipt_path=Path(args.evidence_receipt),
                expected_run_id=args.evidence_run_id,paths=paths,scope=args.scope,
                required_level=args.required_level,facts=facts,s4_confirmed=args.s4_confirmed,
                policy_path=Path(args.policy),job_ledger_path=args.job_ledger,job_id=args.job_id)
        except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError):
            output=controller().refusal("host-evidence-invalid-or-stale",decision.pipeline.value,"unknown")
        output["git_status"],output["commit_status"]=_observe_git(Path(args.workspace))
        output["execution_status"]="EXECUTION_COMPLETE" if output.get("execution_evidence_status")=="VERIFIED" else "UNKNOWN"
        output["declared_paths"]=list(paths)
        output["exclusions"]=[] if output["review_completed"] else [output["reason"]]
        output["state"]="S4_confirmation_required" if output.get("requires_confirmation") else "review_ready"
        print(json.dumps(redact(output),sort_keys=True,indent=2))
        return EXIT_OK if output["review_completed"] and not output["consumption"]["review_blocks_completion"] else EXIT_INCONCLUSIVE
    git_status, commit_status = _observe_git(Path(args.workspace))
    state = NotificationState.S4_CONFIRMATION_REQUIRED if decision.requires_confirmation else NotificationState.REVIEW_READY
    verdict = decision.verdict
    exclusions = list(decision.exclusions)
    if decision.pipeline is not Pipeline.S0 and verdict is not Verdict.INCONCLUSIVE:
        verdict = Verdict.INCONCLUSIVE
        exclusions.append("host-evidence-required")
    # A CLI flag or caller-supplied test status is never independent proof.
    verified = None
    verdict = Verdict.PASS if verified is not None else Verdict.INCONCLUSIVE
    if verified is None:
        exclusions.append("deterministic-validation-not-proven")
    if evaluation is not None and (evaluation.inconclusive or evaluation.findings):
        verdict = Verdict.INCONCLUSIVE
        exclusions.append("caller-contract-findings-require-host-verification")
    findings = list(verified.get("contract_findings", [])) if verified else []
    if evaluation is not None:
        findings.extend(item.canonical_data() for item in evaluation.findings)
    notification = CompletionNotification(
        execution_status="EXECUTION_COMPLETE" if verified is not None else "UNKNOWN",
        review_pipeline="S0" if verified is not None else decision.pipeline.value,
        review_verdict=verdict.value,
        finding_counts={"total": len(findings)},
        git_status=git_status,
        receipt_path=args.receipt or args.evidence_receipt or "",
        commit_status=commit_status,
        state=state,
    )
    if args.s4_action:
        notification = apply_s4_action(notification, S4Action(args.s4_action))
    output = json.loads(notification.to_json())
    output["declared_paths"] = list(paths)
    output["exclusions"] = exclusions
    output["reviewer_invoked"] = False
    # Legacy manual statuses are not independent execution evidence. Consumers
    # must inspect the structured verdict, never interpret exit 0 as PASS.
    from prime_bridge.evidence_adapter import consumption
    output["consumption"] = consumption(verdict.value, load_policy(Path(args.policy)).rollout_mode.value)
    output["evidence_provenance"] = "host_verified" if verified is not None else "caller_reported"
    output["review_completed"] = verified is not None and verdict is Verdict.PASS
    output["verified_run_id"] = verified["run_id"] if verified else None
    if evaluation is not None:
        output["change_contract_evidence"] = evaluation.receipt_data()
        output["contract_findings"] = [item.canonical_data() for item in evaluation.findings]
    output["evaluation_hooks"] = evaluate_event_hooks(
        pipeline=decision.pipeline,
        declared_paths=paths,
        actual_changed_paths=evaluation.actual_changed_paths if evaluation is not None else (),
        evidence_status="VERIFIED" if verified is not None else "UNVERIFIED",
        before_identity=verified["before"]["identity"] if verified else None,
        after_identity=verified["after"]["identity"] if verified else None,
        current_identity=verified["after"]["identity"] if verified else None,
        findings=findings,
        s4_confirmed=args.s4_confirmed,
        policy=load_policy(Path(args.policy)).data,
    )
    print(json.dumps(redact(output), sort_keys=True, indent=2))
    return EXIT_OK if verdict is not Verdict.INCONCLUSIVE else EXIT_INCONCLUSIVE


def _load_valid_receipt(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data,dict) and isinstance(data.get("data"),dict) and data["data"].get("schema_version") == "review-completion.v1":
        from .host import controller
        owner=controller()
        if not path.resolve().is_relative_to((owner.state/"receipts").resolve()):
            raise ValueError("completion receipt is outside the host-owned state")
        return owner._read(path)
    schema = json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(data))
    if errors:
        raise ValueError("invalid receipt: " + "; ".join(error.message for error in errors))
    return data


def cmd_explain(args: argparse.Namespace) -> int:
    print(json.dumps(redact(_load_valid_receipt(Path(args.receipt))), sort_keys=True, indent=2))
    return EXIT_OK


def cmd_remediate(args: argparse.Namespace) -> int:
    receipt = _load_valid_receipt(Path(args.receipt))
    finding = next((item for item in receipt["findings"] if item["finding_id"] == args.finding), None)
    if finding is None:
        raise ValueError(f"finding not present in receipt: {args.finding}")
    task = {
        "kind": "proposed_coding_task",
        "route": "scripts/prime-code-execute.py",
        "finding_id": finding["finding_id"],
        "objective": finding["title"],
        "declared_paths": receipt["declared_paths"],
        "prime_invoked": False,
        "requires_user_selection": True,
    }
    print(json.dumps(redact(task), sort_keys=True, indent=2))
    return EXIT_OK


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", choices=[item.value for item in Scope], required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--required-level", choices=[item.value for item in Pipeline], default="S0")
    parser.add_argument("--unknown-load-bearing", action="store_true")
    parser.add_argument("--changed-lines", type=int, help="Known changed-line count; omitted means unmeasured")
    parser.add_argument("--subsystem-count", type=int, help="Known subsystem count; omitted means unmeasured")
    parser.add_argument("--s4-confirmed", action="store_true")
    parser.add_argument("--routing-facts", help="Closed JSON boolean observations for classification only")
    parser.add_argument("--policy", default=str(REPO_ROOT / "config" / "adaptive-code-review-policy.json"))
    parser.add_argument("--task-packet", help="JSON task packet containing the canonical change contract")
    parser.add_argument("--actual-changed-path", action="append", default=[])
    parser.add_argument("--deterministic-test-result", action="append", default=[], metavar="JSON")
    parser.add_argument("--static-analysis-result", action="append", default=[], metavar="JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adaptive-code-review")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate")
    _common(evaluate); evaluate.set_defaults(func=cmd_evaluate)
    run = commands.add_parser("run")
    run.add_argument("--workspace", default=str(REPO_ROOT))
    run.add_argument("--evidence-receipt")
    run.add_argument("--evidence-run-id")
    run.add_argument("--job-ledger")
    run.add_argument("--job-id")
    _common(run); run.add_argument("--deterministic-pass", action="store_true"); run.add_argument("--s4-action", choices=[item.value for item in S4Action]); run.add_argument("--receipt"); run.set_defaults(func=cmd_run)
    explain = commands.add_parser("explain"); explain.add_argument("--receipt", required=True); explain.set_defaults(func=cmd_explain)
    remediate = commands.add_parser("remediate"); remediate.add_argument("--receipt", required=True); remediate.add_argument("--finding", required=True); remediate.set_defaults(func=cmd_remediate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return EXIT_USAGE
