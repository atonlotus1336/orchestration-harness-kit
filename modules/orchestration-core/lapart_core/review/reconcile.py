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
        if proof is not None and proof not in allowed:
            validation.append(ValueError("unrecognized blocking proof"))
        if validation:
            rejected.append(raw)
            errors.extend(getattr(item, "message", str(item)) for item in validation)
            continue
        accepted.append(dict(raw))
    accepted.sort(key=lambda item: (item["reviewer_id"], item["finding_id"]))
    return Reconciliation(tuple(accepted), tuple(rejected), tuple(errors))
