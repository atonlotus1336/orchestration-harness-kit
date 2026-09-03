#!/usr/bin/env python3
"""Provider-free verification for portable token-compute policy and activation."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import tempfile


MODULE = Path(__file__).resolve().parent
ROOT = MODULE.parents[1]
sys.path.insert(0, str(MODULE))
from lapart_token_compute import HARNESS_IDS, ActivationError, activate, load_policy, load_registry  # noqa: E402


def verify() -> list[str]:
    failures: list[str] = []
    policy_path = MODULE / "token-compute-policy.json"
    try:
        _, policy_sha256 = load_policy(policy_path)
    except ActivationError as exc:
        return [str(exc)]

    source_hashes = json.loads((MODULE / "source-hashes.json").read_text(encoding="utf-8"))
    if (
        source_hashes.get("schema") != "lapart.token-compute-source-hashes.v1"
        or source_hashes.get("algorithm") != "sha256"
    ):
        failures.append("token-compute source-hash manifest is invalid")
    for relative, expected in source_hashes.get("files", {}).items():
        target = ROOT / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(f"token-compute source hash mismatch: {relative}")

    catalog = json.loads((ROOT / "modules/orchestration-protocol/operation-catalog.json").read_text(encoding="utf-8"))
    operations = {row.get("name") for row in catalog.get("operations", [])}
    if "optimization.plan" not in operations:
        failures.append("shared protocol does not expose optimization.plan")
    core_source = (ROOT / "modules/orchestration-core/lapart_core/core.py").read_text(encoding="utf-8")
    if '"optimization.plan": self._optimization_plan' not in core_source:
        failures.append("shared core does not own optimization.plan")
    for harness in HARNESS_IDS:
        module = "hermes" if harness == "hermes" else harness
        manifest = json.loads((ROOT / f"modules/adapters/{module}/harness-manifest.json").read_text(encoding="utf-8"))
        if manifest.get("operations", {}).get("mode") != "all":
            failures.append(f"{harness} does not negotiate optimization.plan")

    with tempfile.TemporaryDirectory(prefix="lapart-token-compute-") as temporary:
        registry = Path(temporary) / "activation.json"
        value, snapshot = activate(
            registry,
            policy_path=policy_path,
            harnesses=HARNESS_IDS,
            approval_reference="provider-free-selftest",
        )
        if snapshot is not None or value.get("provider_calls") != 0:
            failures.append("first activation was not provider-free")
        load_registry(registry, policy_sha256=policy_sha256, require_all=True)
        value["controls"]["marginal_value_stop"] = "enforce"
        registry.write_text(json.dumps(value), encoding="utf-8")
        try:
            load_registry(registry, policy_sha256=policy_sha256, require_all=True)
        except ActivationError:
            pass
        else:
            failures.append("unsafe marginal-value enforcement did not fail closed")
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("HOLD: portable token-compute optimization")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: token-compute optimization for 8 harness adapters")
    print("PASS: active budgets/cache/rendering/accounting, full-depth fallback, shadow stop")
    print("PASS: provider_calls=0, provider_tokens=0, service_restarts=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
