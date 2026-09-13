#!/usr/bin/env python3
"""Manual entry point for deterministic adaptive-review operations."""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if __name__ == "__main__":
    from _shared_runtime import ensure_runtime
    ensure_runtime(__file__)

from prime_bridge.adaptive_review.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
