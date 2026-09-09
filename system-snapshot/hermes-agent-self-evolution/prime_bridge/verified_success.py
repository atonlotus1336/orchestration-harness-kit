"""Independent structural verification for governed execution receipts."""

from __future__ import annotations

import re
from typing import Any, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def is_verified_success(receipt: Mapping[str, Any]) -> bool:
    """Return true only when every independent Prime/OMP/host gate agrees."""
    change_evidence = receipt.get("change_evidence")
    if change_evidence is not None:
        # Change evidence is advisory for semantic-review rollout, but a
        # deterministic FAILED/INCONCLUSIVE collection can never be counted as
        # verified execution success.  Absence preserves legacy receipts.
        if not isinstance(change_evidence, Mapping):
            return False
        if change_evidence.get("status") != "VERIFIED":
            return False
    children = receipt.get("omp_child_job_ids")
    statuses = receipt.get("omp_statuses")
    host = receipt.get("host_apply")
    declared = receipt.get("declared_paths")
    lane = receipt.get("execution_lane")
    if lane == "T2_OMP_LITE":
        parent_gate = (
            receipt.get("prime_active") is False
            and receipt.get("prime_status") == "completed"
            and receipt.get("review_decision") == "not_required"
        )
    else:
        parent_gate = (
            receipt.get("prime_active") is True
            and receipt.get("prime_status") == "completed"
            and receipt.get("review_decision") == "accepted"
        )
    if not (
        receipt.get("outcome") == "complete"
        and receipt.get("integration_authorized") is True
        and parent_gate
        and receipt.get("omp_active") is True
        and isinstance(children, list) and len(children) == 1
        and isinstance(statuses, list) and statuses == ["completed"]
        and type(receipt.get("proposal_count")) is int and receipt["proposal_count"] > 0
        and receipt.get("host_result") == "VERIFIED_OK"
        and isinstance(host, Mapping) and host.get("status") == "VERIFIED_OK"
        and isinstance(declared, list) and declared
    ):
        return False
    applied = host.get("applied")
    test = host.get("test")
    if not (
        isinstance(applied, list) and applied
        and len(applied) == len(set(applied))
        and set(applied).issubset(set(declared))
        and isinstance(test, Mapping)
        and test.get("status") == "ok"
        and test.get("exit_code") == 0
        and isinstance(test.get("checked"), list)
        and set(applied).issubset(set(test["checked"]))
    ):
        return False
    return isinstance(host.get("final_tree_hash"), str) and bool(_SHA256.fullmatch(host["final_tree_hash"]))
