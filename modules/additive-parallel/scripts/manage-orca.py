#!/usr/bin/env python3
"""Launch the governed Orca operator without owning orchestration authority."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prime_bridge.orca_operator import main

if __name__ == "__main__":
    if os.name == "nt":
        # Some Orca Windows builds can report not_running from a file-backed
        # Python __main__. A clean -c child receives the same argv/stdin and
        # uses the identical provider module without shell interpretation.
        command = [
            sys.executable, "-c",
            "from prime_bridge.orca_operator import main; raise SystemExit(main())",
            *sys.argv[1:],
        ]
        raise SystemExit(subprocess.run(command, cwd=ROOT, shell=False).returncode)
    raise SystemExit(main())