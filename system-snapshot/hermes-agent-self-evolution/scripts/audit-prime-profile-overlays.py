#!/usr/bin/env python3
"""Audit every managed profile's Prime overlay without reading secret values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prime_bridge.profile_policy import (  # noqa: E402
    PRESERVED_SECTIONS,
    PrimeProfilePolicy,
    restriction_projection,
)
from prime_bridge.rollout_policy import RolloutPolicy  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--candidate-name", default="config.prime-next.yaml")
    parser.add_argument("--active", action="store_true")
    parser.add_argument("--rollout-policy", type=Path)
    args = parser.parse_args()
    policy = PrimeProfilePolicy.load(args.policy)
    results = []
    for directory in sorted(path for path in args.profiles_root.iterdir() if path.is_dir()):
        source = directory / "config.yaml"
        candidate = source if args.active else directory / args.candidate_name
        receipt_path = directory / "prime-overlay-receipt.json"
        if not source.is_file() or not candidate.is_file() or not receipt_path.is_file():
            continue
        original = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        rendered = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        mode = policy.mode_for(directory.name)
        rendered_servers = rendered.get("mcp_servers") or {}
        if not args.active:
            for section in PRESERVED_SECTIONS:
                if section == "mcp_servers":
                    continue
                if original.get(section) != rendered.get(section):
                    raise SystemExit(f"{directory.name}: preserved section changed: {section}")
            original_servers = original.get("mcp_servers") or {}
            if {key: value for key, value in rendered_servers.items() if key != "prime-agent"} != original_servers:
                raise SystemExit(f"{directory.name}: existing MCP servers changed")
        present = "prime-agent" in rendered_servers
        if present != policy.permits(directory.name):
            raise SystemExit(f"{directory.name}: Prime MCP exposure does not match policy")
        if receipt.get("restriction_fingerprint") != restriction_projection(original)["fingerprint"]:
            raise SystemExit(f"{directory.name}: restriction fingerprint mismatch")
        if receipt.get("activation") != "restart-boundary" or receipt.get("explicit_handoff_required") is not True:
            raise SystemExit(f"{directory.name}: session boundary contract missing")
        results.append({"profile": directory.name, "mode": mode, "prime_mcp": present})
    if not results:
        raise SystemExit("no staged profile overlays found")
    output = {
        "status": "PASS", "profiles": len(results),
        "prime_available": sum(item["prime_mcp"] for item in results),
        "native_only": [item["profile"] for item in results if not item["prime_mcp"]],
        "preserved_sections": list(PRESERVED_SECTIONS),
    }
    if args.rollout_policy is not None:
        rollout = RolloutPolicy.load(args.rollout_policy)
        eligible = [item for item in results if item["profile"] not in rollout.excluded_profiles]
        if not eligible:
            raise SystemExit("no eligible profiles found")
        available = [item for item in eligible if item["prime_mcp"] and rollout.profile_eligible(item["profile"])]
        coverage = (100 * len(available)) // len(eligible)
        if coverage < rollout.minimum_eligible_coverage_percent:
            raise SystemExit("Prime eligible-profile coverage is below rollout policy")
        output["rollout"] = {
            "stage": rollout.stage, "eligible_profiles": len(eligible),
            "eligible_available": len(available), "coverage_percent": coverage,
            "minimum_percent": rollout.minimum_eligible_coverage_percent,
            "excluded_profiles": sorted(rollout.excluded_profiles),
        }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
