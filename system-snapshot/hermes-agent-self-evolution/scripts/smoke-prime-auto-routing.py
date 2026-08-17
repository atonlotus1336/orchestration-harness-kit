#!/usr/bin/env python3
"""Controlled Phase 9 routing proof; it never starts a gateway or Prime worker."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prime_bridge.intent_router import IntentPolicy, classify  # noqa: E402


def main() -> int:
    policy = IntentPolicy.load(ROOT / "config/prime-auto-route-policy.json")
    skill = ROOT.parent / "skills-shared/ponytail/skills/ponytail/SKILL.md"
    ponytail = {
        "available": True, "path": policy.ponytail_path, "intensity": "full",
        "fingerprint": hashlib.sha256(skill.read_bytes()).hexdigest(),
    }
    positives = {
        "gsd_plan": "make a gsd plan", "gsd_execute": "execute phase 9",
        "phase_check": "phase check 9", "doublecheck": "doublecheck phase 9",
        "audit": "audit phase 9", "debug": "debug this", "diagnose": "diagnose this",
        "bug_fix": "bug fix this",
    }
    results = []
    for family, message in positives.items():
        result = classify(message, profile="workspace", mode="prime-default", readiness=True,
                          compatibility=True, admitted=True, ponytail=ponytail, policy=policy)
        results.append(result["outcome"] == "PRIME_AUTO" and result["family"] == family)
    negatives = [
        classify("What is Prime Agent?", profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail=ponytail, policy=policy),
        classify("audit phase 9", profile="native-only-test", mode="native-only", readiness=True, compatibility=True, admitted=True, ponytail=ponytail, policy=policy),
        classify("audit phase 9", profile="workspace", mode="prime-default", readiness=True, compatibility=True, admitted=True, ponytail={"available": False}, policy=policy),
    ]
    results.extend(item["outcome"] == "NATIVE" for item in negatives)
    print(json.dumps({"status": "PASS" if all(results) else "FAIL", "cases": len(results), "live_execution": False}, sort_keys=True))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
