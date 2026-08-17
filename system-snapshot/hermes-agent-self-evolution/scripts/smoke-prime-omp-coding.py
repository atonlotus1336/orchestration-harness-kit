#!/usr/bin/env python3
"""Controlled Phase 10 Plan 06 smoke: Prime-to-OMP path (no live gateway)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prime_bridge.intent_router import IntentPolicy, classify  # noqa: E402
from prime_bridge.omp_adapters import create_hash_edit_proposal, validate_lsp_request  # noqa: E402
from prime_bridge.omp_policy import OmpCodingPolicy, admit_omp  # noqa: E402
from prime_bridge.omp_runtime import LspRegistry  # noqa: E402
from prime_bridge.omp_worktree import OmpWorktree  # noqa: E402

POLICY = IntentPolicy.load(ROOT / "config/prime-auto-route-policy.json")
OMP_POLICY = OmpCodingPolicy.load(ROOT / "config/omp-coding-policy.json")
LSP = LspRegistry.load(ROOT / "config/omp-lsp-servers.json")
SKILL = ROOT.parent / "skills-shared/ponytail/skills/ponytail/SKILL.md"


def ponytail():
    return {"available": True, "path": POLICY.ponytail_path, "intensity": "full", "fingerprint": hashlib.sha256(SKILL.read_bytes()).hexdigest()}


def main() -> int:
    results = []
    # Positive: LSP adapter
    request, server = validate_lsp_request(
        job_id="a" * 32, profile="workspace", project_root=ROOT, language="python",
        method="textDocument/hover", params={"textDocument": {"uri": "file:///x.py"}},
        lsp_registry=LSP, timeout_seconds=10, item_limit=100, byte_limit=100_000,
    )
    results.append(request.method == "textDocument/hover" and server.read_only)
    # Positive: hash-edit preview
    proposal = create_hash_edit_proposal(
        job_id="a" * 32, profile="workspace", project_root=ROOT,
        relative_path="file.py", before_bytes=b"x", proposed_bytes=b"y",
        capability_receipt="c" * 64,
    )
    results.append(proposal.before_hash == hashlib.sha256(b"x").hexdigest())
    # Positive: OMP admission
    receipt = admit_omp(
        feasibility={"verdict": "READY", "captured_at": "2026-08-13T00:00:00Z"},
        prime_job={"job_id": "a" * 32, "status": "running"}, project_root=ROOT,
        profile="workspace",
        model_route={"status": "READY", "compatibility": {"supports_tools": True, "supports_streaming": True}, "explicit_fallback": None, "route": {"fingerprint": "b" * 64}},
        capability_policy={"enabled": True}, omp_policy=OMP_POLICY, shared_budget={"status": "READY"},
    )
    results.append(receipt["status"] == "ADMITTED")
    # Positive: worker patch (small temp project, not the whole repo)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "file.py").write_text("x = 1\n")
        worktree = OmpWorktree(tmp_path)
        worktree.populate()
        (worktree.worktree / "file.py").write_text("x = 2\n")
        patch = worktree.export_patch(changed_paths=["file.py"])
        results.append(patch["changed_paths"] == ["file.py"])
    # Positive: native stays native
    native = classify("What is Prime Agent?", profile="workspace", mode="prime-default",
                      readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    results.append(native["outcome"] == "NATIVE")
    # Positive: Prime-only
    prime = classify("audit phase 9", profile="workspace", mode="prime-default",
                     readiness=True, compatibility=True, admitted=True, ponytail=ponytail(), policy=POLICY)
    results.append(prime["outcome"] == "PRIME_AUTO")
    print(json.dumps({"status": "PASS" if all(results) else "FAIL", "cases": len(results), "live_execution": False}, sort_keys=True))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
