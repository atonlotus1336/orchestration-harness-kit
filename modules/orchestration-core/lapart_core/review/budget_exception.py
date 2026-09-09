"""Inactive-by-default extension of the existing trusted review controller.

No approval issuer, provider switch, scheduler, or independent budget ledger.
Only the existing owner may supply a signed human approval. All I/O is scoped
to that owner's state. Python callers share the controller's trusted boundary.
"""
import hashlib
import math
import os
from pathlib import Path
import re
import time
from types import MappingProxyType

from .adapters import _safe_path
from .catalog import load_frozen_catalog
from .policy import load_policy
from .prompts import review_prompt, finding_schema
from .proofs import HostCheck, HostTestVerifier
from .transport import JsonReviewTransport

EXPANDABLE = {"max_context_bytes", "max_estimated_input_tokens", "max_total_tokens"}
CUMULATIVE = {"max_model_calls": "model_requests", "max_estimated_input_tokens": "estimated_input_units",
              "max_output_tokens": "output_units", "max_total_tokens": "total_units",
              "max_tool_calls": "tool_calls", "max_elapsed_seconds": "elapsed_seconds"}
_SEAL = object()


class _Grant:
    def __init__(self, seal, pipeline, limits, sources, packets):
        if seal is not _SEAL:
            raise ValueError("unowned budget grant")
        self.seal, self.pipeline, self.used = seal, pipeline, False
        self.limits = MappingProxyType(dict(limits))
        self.sources = MappingProxyType(dict(sources))
        self.packets = MappingProxyType(dict(packets))


def take_grant(grant, pipeline, sources):
    if (type(grant) is not _Grant or grant.seal is not _SEAL or grant.used
            or grant.pipeline != pipeline or dict(grant.sources) != dict(sources)):
        raise ValueError("invalid, stale or reused owner budget grant")
    grant.used = True
    return dict(grant.limits), dict(grant.packets)


def _limits(requested, base, ledger):
    if set(requested) != set(base) or any(type(v) is not int or v < 0 for v in requested.values()):
        raise ValueError("invalid exception limits")
    if any(requested[k] > base[k] for k in base if k not in EXPANDABLE):
        raise ValueError("exception cannot increase calls, output, time, tools or file limits")
    # Hard resource ceiling even for a signed approval, independent of models.
    if requested["max_context_bytes"] > 1048576 or requested["max_estimated_input_tokens"] > 4000000 or requested["max_total_tokens"] > 5000000:
        raise ValueError("exception exceeds resource ceiling")
    result = dict(requested)
    for key in base:
        if key not in EXPANDABLE:
            result[key] = min(result[key], ledger["limits"][key])
    for key, field in CUMULATIVE.items():
        spent = ledger["spent"].get(field, 0)
        if type(spent) not in (int, float) or not math.isfinite(spent) or spent < 0:
            raise ValueError("invalid cumulative usage")
        result[key] = max(0, result[key] - math.ceil(spent))
    return result


def _context(owner, *, workspace, evidence, pipeline, scope, current_identity, limits):
    from .controller import digest
    pipeline = str(getattr(pipeline, "value", pipeline))
    if pipeline not in {"S1", "S2", "S3"} or scope not in {"phase", "plan"}:
        raise ValueError("exception is restricted to admitted S1-S3 plan or phase review")
    root = Path(workspace).resolve()
    if evidence.get("stage") != "finished" or evidence.get("execution_evidence_status") != "VERIFIED":
        raise ValueError("verified host evidence required")
    if current_identity() != evidence["after"]["identity"]:
        raise ValueError("stale host evidence")
    policy = load_policy()
    packet = evidence["packet"]
    lineage = digest({"workspace": str(root), "harness": evidence["harness"],
        "session": evidence["binding"].get("session_binding_sha256"), "task": packet["objective"]})
    ledger_path = owner.state / "lineages" / (lineage + ".json")
    ledger = owner._read(ledger_path) if ledger_path.exists() else {
        "deliveries": 0, "limits": policy.data["budgets"][pipeline], "spent": {}}
    if ledger.get("unknown_usage") or ledger["deliveries"] >= owner.config["max_deliveries"]:
        raise ValueError("lineage usage unknown or delivery limit reached")
    remaining = _limits(limits, policy.data["budgets"][pipeline], ledger)
    paths = tuple(packet["allowed_paths"])
    if not paths or len(paths) != len(set(paths)) or len(paths) > remaining["max_files"]:
        raise ValueError("invalid exception scope")
    from .context import review_material, bind_review_context
    contents, sources, review_context = review_material(
        root, evidence, remaining["max_context_bytes"])
    catalog = load_frozen_catalog(owner.catalog_path, vault_root=owner.vault, readable_files=paths)
    ids = sorted(set(policy.data["pipelines"][pipeline]) & set(catalog.reviewers))
    expected = {v for v in policy.data["pipelines"][pipeline]
                if v not in {"facts", "deterministic_checks", "receipt", "triggered_specialist"}}
    if set(ids) != expected or not ids or not all(catalog.verify_skill(v) for v in ids):
        raise ValueError("mandatory reviewer catalog is incomplete or stale")
    transport = JsonReviewTransport(**owner.config["provider"])
    transport.validate()
    registered = owner.config.get("checks", {}).get(str(root), {})
    verifier = HostTestVerifier(root, {k: HostCheck(**v) for k, v in registered.items()}, sources) if registered else None
    descriptions = verifier.descriptions() if verifier else ()
    packets = {name: transport.payload(bind_review_context(
                                      review_prompt(name, catalog.reviewers[name].lens, finding_schema(), descriptions),
                                      review_context),
                                      contents, remaining["max_output_tokens"]) for name in ids}
    from .packets import admit_packets, finalize_packets
    packets = finalize_packets(packets, remaining)
    plan = admit_packets(packets, max_request_bytes=remaining["max_context_bytes"],
        max_input_units=remaining["max_estimated_input_tokens"], max_calls=remaining["max_model_calls"])
    if current_identity() != evidence["after"]["identity"]:
        raise ValueError("source changed during exception preparation")
    binding = {"workspace": str(root), "harness": evidence["harness"], "run_id": evidence["run_id"],
        "packet": evidence["binding"]["packet_hash"], "evidence": digest(evidence),
        "approval_scope": {
            "job_id": evidence.get("lifecycle_binding", {}).get("job_id") or evidence["binding"].get("lifecycle", {}).get("owner_id")},
        "pipeline": pipeline, "scope": scope, "lineage": lineage, "ledger": digest(ledger),
        "policy": policy.fingerprint, "configuration": digest(owner.config),
        "catalog": hashlib.sha256(owner.catalog_path.read_bytes()).hexdigest(),
        "skills": {v: catalog.reviewers[v].skill_sha256 for v in ids},
        "implementation": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")},
        "sources": sources, "packets": plan["packet_sha256"]}
    return binding, remaining, plan


def prepare(owner, *, expires_at, limits, s4_confirmed=False, deadline_seconds=None, **context):
    from .controller import atomic, digest
    if type(expires_at) is not int or not time.time() < expires_at <= time.time() + 86400:
        raise ValueError("exception expiry must be within one day")
    if deadline_seconds is not None:
        raise ValueError("prepare exception without a separate deadline override")
    binding, remaining, plan = _context(owner, limits=limits, **context)
    proposal = {"schema_version": "review-budget-exception.v1", "status": "draft", "expires_at": expires_at,
                "binding": binding, "limits": dict(limits), "remaining": remaining, "packet_plan": plan}
    identifier = digest(proposal)
    atomic(owner.state / "budget-exceptions" / (identifier + ".json"), owner._signed(proposal))
    return dict(proposal, id=identifier)


def consume(owner, identifier, **context):
    from .controller import digest
    if owner.config.get("budget_exceptions_enabled", False) is not True:
        raise ValueError("budget exceptions are disabled")
    if not isinstance(identifier, str) or not re.fullmatch("[a-f0-9]{64}", identifier):
        raise ValueError("invalid exception identifier")
    proposal = owner._read(owner.state / "budget-exceptions" / (identifier + ".json"))
    if digest(proposal) != identifier or proposal.get("status") != "draft" or proposal["expires_at"] <= time.time():
        raise ValueError("altered or expired exception")
    from .budget_approval import validate
    validate(owner, identifier, proposal)
    binding, remaining, plan = _context(owner, limits=proposal["limits"], **context)
    if binding != proposal["binding"] or remaining != proposal["remaining"] or plan != proposal["packet_plan"]:
        raise ValueError("exception payload or cumulative lineage changed")
    # Called under the existing lineage lock; O_EXCL also excludes cross-lineage reuse.
    fd = os.open(owner.state / "budget-exceptions" / (identifier + ".claim"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    return _Grant(_SEAL, binding["pipeline"], remaining, binding["sources"], binding["packets"])
