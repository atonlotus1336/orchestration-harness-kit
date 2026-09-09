"""Strict loading and cross-field validation for adaptive-review policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator

from .models import LoadedPolicy

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPO_ROOT / "review-policy.json"
DEFAULT_SCHEMA = REPO_ROOT / "review-policy.schema.json"


class PolicyError(ValueError):
    """Raised when policy cannot be trusted."""


def canonical_policy_bytes(data: Mapping[str, Any]) -> bytes:
    canonical = {key: value for key, value in data.items() if key != "$schema"}
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def policy_fingerprint(data: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_policy_bytes(data)).hexdigest()


def validate_cross_fields(data: Mapping[str, Any]) -> None:
    if data["selection"]["thresholds"]["S4"].get("automatic_start") is not False:
        raise PolicyError("S4 automatic_start must be false")
    if data["budgets"]["S0"]["max_model_calls"] != 0:
        raise PolicyError("S0 must have zero model calls")
    for level, members in data["pipelines"].items():
        if "improve" in members and level != "S4":
            raise PolicyError("improve is permitted only in S4")
    separation = data["execution_review_separation"]
    if separation["mapping"] != "none":
        raise PolicyError("T0-T4 execution lanes cannot map to S0-S4 review pipelines")
    if data["scopes"]["system"].get("manual_only") is not True:
        raise PolicyError("system scope must remain manual-only")
    if data["scopes"]["plan"].get("s4_confirmation_required") is not True:
        raise PolicyError("plan S4 must require confirmation")
    hooks = data["evaluation_hooks"]
    if hooks["automatic"] is not True or hooks["model_calls"] != 0:
        raise PolicyError("evaluation hooks must be automatic with zero model calls")
    if hooks["learning_candidate"]["direct_promotion"] is not False:
        raise PolicyError("evaluation hooks cannot replace the existing learning owner")


def load_policy(path: Path = DEFAULT_POLICY, schema_path: Path = DEFAULT_SCHEMA) -> LoadedPolicy:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(str(exc)) from exc
    errors = sorted(Draft7Validator(schema).iter_errors(data), key=lambda item: list(item.path))
    if errors:
        rendered = "; ".join(f"{'/'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors)
        raise PolicyError(rendered)
    validate_cross_fields(data)
    return LoadedPolicy(data=data, fingerprint=policy_fingerprint(data))


# The general selector still cannot automatically start S4. Its separate,
# host-configured conditional route is authorized only by this policy owner.
_S4_SEAL = object()


class _S4Grant:
    def __init__(self, seal, owner, workspace, evidence, admission):
        if seal is not _S4_SEAL:
            raise PolicyError("unowned S4 grant")
        self.seal, self.owner = seal, owner
        self.workspace = str(Path(workspace).resolve())
        self.evidence = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        self.source = evidence["after"]["identity"]
        self.sources = {
            path: evidence["after"]["contents"][path]
            for path in evidence["packet"]["allowed_paths"]
        }
        self.authority = owner._authority_fingerprint()
        self.admission = json.loads(json.dumps(admission))


def issue_s4_grant(owner, *, workspace, evidence, admission):
    """Issue a sealed grant after the existing dispatch owner records its claim."""
    harness = evidence["harness"]
    if (owner.mode(harness) != "enforced"
            or harness not in owner.config.get("automatic_s4", {}).get("enabled_harnesses", [])
            or evidence.get("stage") != "finished"
            or evidence.get("execution_evidence_status") != "VERIFIED"
            or admission.get("status") != "CANDIDATE"
            or not admission.get("scenarios")):
        raise PolicyError("conditional S4 owner admission missing")
    from .controller import digest
    key = digest({"workspace": str(Path(workspace).resolve()),
                  "candidate": admission["deduplication_key"]})
    claim = owner._read(owner.state / "automatic-s4" / (key + ".json"))
    if claim.get("status") != "pending" or claim.get("admission") != admission:
        raise PolicyError("S4 atomic claim missing or changed")
    return _S4Grant(_S4_SEAL, owner, workspace, evidence, admission)


def validate_s4_grant(grant, *, owner=None, workspace=None, evidence=None,
                      scope="phase", source_identity=None, source_hashes=None):
    if (type(grant) is not _S4Grant or grant.seal is not _S4_SEAL or scope != "phase"
            or owner is not None and grant.owner is not owner
            or workspace is not None and grant.workspace != str(Path(workspace).resolve())
            or source_identity is not None and grant.source != source_identity
            or evidence is not None and grant.evidence != hashlib.sha256(
                json.dumps(evidence, sort_keys=True).encode()).hexdigest()
            or grant.authority != grant.owner._authority_fingerprint()):
        raise PolicyError("unowned, stale or mismatched S4 grant")
    if source_hashes is not None:
        expected = {
            path: hashlib.sha256(b"").hexdigest() if value == "ABSENT" else value
            for path, value in grant.sources.items()
        }
        if dict(source_hashes) != expected:
            raise PolicyError("S4 review sources changed")
    goal = grant.admission.get("goal_binding")
    if goal and hashlib.sha256(Path(goal["path"]).read_bytes()).hexdigest() != goal["sha256"]:
        raise PolicyError("S4 goal changed")
    return True
