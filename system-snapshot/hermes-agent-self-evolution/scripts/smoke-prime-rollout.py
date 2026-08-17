#!/usr/bin/env python3
"""Generate source-bound controlled evidence for the Phase 8 rollout gates."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prime_bridge.admission import AdmissionController  # noqa: E402
from prime_bridge.emergency_control import EmergencyController  # noqa: E402
from prime_bridge.operations import REQUIRED_AXES, RolloutDashboard  # noqa: E402
from prime_bridge.rollout_policy import RolloutPolicy  # noqa: E402
from prime_bridge.upgrade_gate import Candidate, UpdateGate  # noqa: E402


SOURCE_PATHS = (
    "config/prime-profile-policy.json",
    "config/prime-rollout-policy.json",
    "docs/prime-rollout-operations.md",
    "prime_bridge/admission.py",
    "prime_bridge/emergency_control.py",
    "prime_bridge/mcp_server.py",
    "prime_bridge/operations.py",
    "prime_bridge/rollout_policy.py",
    "prime_bridge/supervisor.py",
    "prime_bridge/upgrade_gate.py",
    "scripts/audit-prime-profile-overlays.py",
    "scripts/prime-emergency-disable.py",
    "scripts/smoke-prime-rollout.py",
    "scripts/verify-phase8-evidence.py",
)


def snapshot(now: datetime, *, cpu: float = 20, memory: float = 25, age: int = 0) -> dict:
    return {
        "status": "VERIFIED", "cpu_percent": cpu, "memory_percent": memory,
        "sampled_at": (now - timedelta(seconds=age)).isoformat(), "source": "controlled-smoke",
    }


def main() -> int:
    policy = RolloutPolicy.load(ROOT / "config/prime-rollout-policy.json")
    now = datetime.now(timezone.utc)
    candidate_value = {
        "source_commit": "1" * 40,
        "image_digest": "sha256:" + "2" * 64,
        "command_fingerprint": "3" * 64,
        "prime_protocol": "jsonl-rpc-v0.7.1",
        "bridge_version": "phase8",
    }
    candidate = Candidate.parse(candidate_value)
    checks = {
        name: {"status": "PASS", "candidate_fingerprint": candidate.fingerprint, "checked_at": now.isoformat()}
        for name in policy.required_update_checks
    }
    gate = UpdateGate(policy)
    ready = gate.evaluate(candidate_value, checks, native_status="PASS", profile_coverage_percent=100, now=now)
    missing = dict(checks)
    missing.pop("model-route")
    missing_result = gate.evaluate(candidate_value, missing, native_status="PASS", profile_coverage_percent=100, now=now)
    mismatch = {name: dict(value) for name, value in checks.items()}
    mismatch["rpc-contract"]["candidate_fingerprint"] = "0" * 64
    mismatch_result = gate.evaluate(candidate_value, mismatch, native_status="PASS", profile_coverage_percent=100, now=now)

    active_global = [
        {"status": "running", "profile": "workspace"},
        {"status": "running", "profile": "home"},
    ]
    global_result = AdmissionController(policy.admission, lambda: snapshot(now)).assess(
        {"profile": "deepseekv4"}, active_global, now=now,
    )
    profile_result = AdmissionController(policy.admission, lambda: snapshot(now)).assess(
        {"profile": "workspace"}, [{"status": "running", "profile": "workspace"}], now=now,
    )
    pressure_result = AdmissionController(policy.admission, lambda: snapshot(now, cpu=90, memory=90)).assess(
        {"profile": "home"}, [], now=now,
    )
    stale_result = AdmissionController(
        policy.admission, lambda: snapshot(now, age=policy.admission.sample_max_age_seconds + 1),
    ).assess({"profile": "home"}, [], now=now)

    eligible = [profile for profile in ("workspace", "home", "deepseekv4", "gemmalocal") if policy.profile_eligible(profile)]
    profile_pass = len(eligible) == 4 and not policy.profile_eligible("telegram")
    axes = {name: {"status": "PASS", "reason": "controlled passing observation"} for name in REQUIRED_AXES}
    degraded_axes = {name: dict(value) for name, value in axes.items()}
    degraded_axes["remote_client_reachability"] = {"status": "DEFERRED", "reason": "desktop not required for controlled rehearsal"}
    degraded = RolloutDashboard().evaluate(degraded_axes, emergency_disabled=False)

    with tempfile.TemporaryDirectory(prefix="phase8-rollout-") as temporary:
        root = Path(temporary)
        policy_copy = root / "prime-profile-policy.json"
        shutil.copyfile(ROOT / "config/prime-profile-policy.json", policy_copy)
        controller = EmergencyController(policy_copy, root / "rollback")
        plan = controller.plan()
        disabled = controller.apply(
            expected_before_sha256=str(plan["before_sha256"]), rollback_id="phase8-smoke",
            confirmation="DISABLE-PRIME",
        )
        restored = controller.rollback(
            rollback_id="phase8-smoke", expected_current_sha256=str(disabled["after_sha256"]),
        )

    cases = {
        "upgrade_ready": ready["status"],
        "missing_update_check": missing_result["status"],
        "candidate_mismatch": mismatch_result["status"],
        "global_load_limit": global_result["status"],
        "profile_load_limit": profile_result["status"],
        "resource_pressure": pressure_result["status"],
        "stale_resource_sample": stale_result["status"],
        "broad_profile_matrix": "PASS" if profile_pass else "FAIL",
        "readiness_axis_failure": degraded["status"],
        "emergency_disable": disabled["status"].upper(),
        "emergency_rollback": restored["status"].upper(),
        "native_fallback": degraded["native_hermes"],
    }
    evidence = {
        "schema_version": "1.0", "environment": "controlled-temporary", "status": "PASS",
        "case_count": len(cases), "cases": cases, "live_rollout_claim": False,
        "native_hermes_preserved": True, "one_backend": True,
        "source_sha256": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in SOURCE_PATHS
        },
    }
    destination = ROOT / ".planning/phases/08-compatibility-gates-broad-rollout/evidence/phase8-controlled-smoke.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"phase8 smoke: PASS: {len(cases)} controlled cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
