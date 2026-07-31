"""M15 adopt-gate — the ONLY path from a learning digest into memory.

Explicitly human-invoked, one item at a time. Writes an auto-memory note through the
M2 capture path (a dated note in your notes directory that the spool ingests with
LLM-authored gists + provenance). Never touches routing, config, or behavior; if an
adopted item implies a behavior change, that change is separate work gated by
eval-run.ps1 per the loop contract.4. There is NO batch mode and NO auto mode by design.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

AUTO_MEMORY = Path(os.environ.get("LEARNING_LOOP_MEMORY_NOTES", r"<vault>\.claude\auto-memory"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", type=Path, required=True, help="the LEARNING-DIGEST-<week>.md file")
    ap.add_argument("--item", required=True, help="exact surfaced item name to adopt")
    ap.add_argument("--approved-by", required=True, help="human approver name (recorded as provenance)")
    args = ap.parse_args()

    text = args.digest.read_text(encoding="utf-8")
    m = re.search(rf"###\s+\d+\.\s+{re.escape(args.item)}\s*\(confidence[^)]*\)\s*\n(.*?)(?=\n###|\n## |\Z)",
                  text, re.S)
    if not m:
        print(f"REFUSED: item '{args.item}' is not a surfaced item in {args.digest.name} "
              "(only surfaced items are adoptable; discarded/quarantined never are)")
        return 1
    block = m.group(1)
    claim = re.search(r"- claim: (.+)", block)
    verify = re.search(r"- verify: (.+)", block)
    source = re.search(r"- source: (.+)", block)

    stamp = time.strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", args.item.lower()).strip("-")[:40]
    AUTO_MEMORY.mkdir(parents=True, exist_ok=True)
    note = AUTO_MEMORY / f"{stamp}-adopted-{slug}.md"
    n = 2
    while note.exists():  # collision guard (same-day truncated slugs must never overwrite)
        note = AUTO_MEMORY / f"{stamp}-adopted-{slug}-{n}.md"
        n += 1
    note.write_text(
        f"# Adopted from learning loop: {args.item} ({stamp})\n\n"
        f"Provenance: {args.digest.name} | source transcript: {source.group(1) if source else 'n/a'} | "
        f"approved-by: {args.approved_by} (M15 adopt-gate)\n\n"
        f"Claim: {claim.group(1) if claim else 'n/a'}\n"
        f"Verify: {verify.group(1) if verify else 'n/a'}\n\n"
        "Adopted as a KNOWLEDGE note only. Any behavior/routing/config change implied by this "
        "tool is separate work and must pass eval-run.ps1 before merging (the loop contract.4).\n",
        encoding="utf-8",
    )
    print(f"ADOPTED: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
