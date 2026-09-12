#!/usr/bin/env python3
"""Register, move, or safely remove an optional parallel provider."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prime_bridge.orchestration_switch import default_state_path
from prime_bridge.parallel_provider_manager import (
    ParallelProviderManager, ProviderManagerError,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("status", "bind", "register", "unregister", "uninstall", "restore"))
    parser.add_argument("--consumer", action="append", choices=("router", "evaluator"))
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--allowed-root", type=Path, action="append")
    parser.add_argument("--snapshot")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--state-root", type=Path, default=default_state_path().parent)
    parser.add_argument("--backup-root", type=Path, default=ROOT / ".state" / "parallel-provider-backups")
    args = parser.parse_args(argv)
    try:
        manager = ParallelProviderManager(
            root=args.root,
            manifest=args.root / "config" / "orca-provider.manifest.json",
            catalog=args.root / "config" / "parallel-worker-providers.json",
            capability_policy=args.root / "config" / "prime-capability-policy.json",
            switch_path=args.state_root / "orchestration-switches.json",
            backup_root=args.backup_root,
        )
        if args.operation == "status":
            result = manager.status()
            result["status"] = "PASS"
        elif args.operation == "bind":
            result = manager.bind(args.consumer or (), apply=args.apply)
        elif args.operation == "register":
            result = manager.register(apply=args.apply)
        elif args.operation == "restore":
            result = manager.restore(args.snapshot or "", apply=args.apply)
        else:
            if args.ledger is None:
                raise ProviderManagerError("--ledger is required for removal")
            result = manager.unregister(
                ledger=args.ledger,
                allowed_roots=tuple(args.allowed_root or (ROOT.parent,)),
                apply=args.apply, uninstall=args.operation == "uninstall",
            )
    except (OSError, ValueError, json.JSONDecodeError, ProviderManagerError) as exc:
        result = {"status": "BLOCKED", "operation": args.operation, "reason": str(exc)}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"PASS", "DRY-RUN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
