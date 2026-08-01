#!/usr/bin/env python
"""SessionStart hook — read-on-write digest reader (context-savings).
Emits a recent-memory snapshot as additionalContext. Instant (reads a JSON
snapshot; no chromadb import). Fail-open: missing/unreadable -> emit nothing.
Bootstrap rewrites __STORE__ to your store path."""
import json, os, sys
from pathlib import Path

CANON = Path(os.environ.get("MEMORYWEAVER_DATA_DIR") or "__STORE__")
DIGEST = CANON / "recent-digest.json"
MAX = 8


def main() -> None:
    try:
        sys.stdin.read()
    except Exception:
        pass
    try:
        if not DIGEST.exists():
            return
        payload = json.loads(DIGEST.read_text(encoding="utf-8"))
        stubs = (payload.get("stubs") or [])[:MAX]
        if not stubs:
            return
        lines = ["MemoryWeaver — %d recent memories (read-on-write digest). "
                 "Use memory_stub_list / memory_recall for more; memory_fetch <id> for full text:" % len(stubs)]
        for s in stubs:
            conf = (s.get("confidence") or "?")[:3]
            proj = s.get("project") or "-"
            gist = (s.get("gist") or "").strip()
            ik = (s.get("identity_key") or "")[:12]
            lines.append(f"- ({conf}/{proj}) {gist}  ·{ik}")
        out = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                      "additionalContext": "\n".join(lines)}}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass


if __name__ == "__main__":
    main()
