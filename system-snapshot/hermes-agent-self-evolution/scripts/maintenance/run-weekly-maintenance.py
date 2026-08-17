#!/usr/bin/env python3
"""Run a deterministic maintenance simulation.  Live adapters are intentionally absent."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from prime_bridge.maintenance import CandidateResult, MaintenanceController
from prime_bridge.maintenance_ledger import MaintenanceLedger


class DryRunAdapter:
    def snapshot(self, component: str) -> dict[str, str]:
        return {"component": component, "source_ref": "dry-run-known-good", "runtime_digest": "dry-run"}
    def update_candidate(self, component: str) -> CandidateResult:
        return CandidateResult(True, True, True, True, evidence={"component": component, "desktop": "DEFERRED"})
    def restore(self, component: str, snapshot: dict[str, str]) -> bool:
        return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--ledger", type=Path, default=ROOT / "output" / "maintenance" / "maintenance.sqlite3")
    args = parser.parse_args()
    controller = MaintenanceController(policy_path=ROOT / "config" / "verified-maintenance-policy.json", ledger=MaintenanceLedger(args.ledger), adapter=DryRunAdapter())
    import json
    print(json.dumps(controller.run(dry_run=True), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
