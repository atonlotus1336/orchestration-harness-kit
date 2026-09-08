"""Normalize reviewer findings without converting prose into blocking proof."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class Reconciliation:
    findings: tuple[dict, ...]
    rejected: tuple[object, ...]
    errors: tuple[str, ...]

    @property
    def blocking(self) -> tuple[dict, ...]:
        return tuple(item for item in self.findings if item.get("blocking_proof"))


def reconcile_outputs(
    outputs: Iterable[object],
    finding_schema: Mapping[str, object],
    allowed_blocking_proofs: Iterable[str],
    *, expected_reviewer_id=None, proof_verifier=None, budget=None,
) -> Reconciliation:
    validator = Draft7Validator(finding_schema)
    allowed = set(allowed_blocking_proofs)
    accepted: list[dict] = []
    rejected: list[object] = []
    errors: list[str] = []
    for raw in outputs:
        if not isinstance(raw, dict):
            rejected.append(raw)
            errors.append("finding is not an object")
            continue
        validation = sorted(validator.iter_errors(raw), key=lambda item: list(item.path))
        proof = raw.get("blocking_proof")
        if expected_reviewer_id is not None and raw.get("reviewer_id") != expected_reviewer_id:
            validation.append(ValueError("reviewer identity mismatch"))
        if proof is not None and proof not in allowed:
            validation.append(ValueError("unrecognized blocking proof"))
        if validation:
            rejected.append(raw)
            errors.extend(getattr(item, "message", str(item)) for item in validation)
            continue
        item = dict(raw)
        from .proofs import HostTestVerifier
        verified = (expected_reviewer_id is not None and type(proof_verifier) is HostTestVerifier
                    and budget is not None and proof_verifier.verify(item, budget))
        if not verified:
            item["blocking_proof"] = None
            item["advisory"] = True
        else:
            item["advisory"] = False
        accepted.append(item)
    accepted.sort(key=lambda item: (item["reviewer_id"], item["finding_id"]))
    return Reconciliation(tuple(accepted), tuple(rejected), tuple(errors))
