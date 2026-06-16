"""
/memory-browse command — deterministic metadata-filter browse.

No decay applied (deterministic). Defaults to active project scope (fixes E-007).

Usage:
  /memory-browse
  /memory-browse --wing <name>
  /memory-browse --room <name>
  /memory-browse --drawer <name>
  /memory-browse --tag <tag>
  /memory-browse --confidence explicit|inferred|all
  /memory-browse --project <name>
  /memory-browse --all-projects       (explicit cross-project flag)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..store.chroma import ChromaDBAdapter


def run(argv: list[str]) -> str:
    """Entry point — returns markdown-formatted browse results."""
    parser = argparse.ArgumentParser(prog="memory-browse", add_help=False)
    parser.add_argument("--wing", default=None)
    parser.add_argument("--room", default=None)
    parser.add_argument("--drawer", default=None)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--confidence", choices=["explicit", "inferred", "all"], default="explicit")
    parser.add_argument("--project", default=None)
    parser.add_argument("--all-projects", action="store_true")

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return "_Usage: /memory-browse [--wing W] [--room R] [--drawer D] [--tag T] [--confidence explicit|inferred|all] [--project P] [--all-projects]_"

    # Project scoping (fixes E-007)
    project: str | None
    if args.all_projects:
        project = None
    elif args.project:
        project = args.project
    else:
        project = _infer_active_project()

    store = ChromaDBAdapter()
    records = store.query_browse(
        project=project,
        wing=args.wing,
        room=args.room,
        drawer=args.drawer,
        tag=args.tag,
        confidence=args.confidence,
    )

    return _format_browse(records, project, args)


def _format_browse(records, project, args) -> str:
    scope_desc = project or "all projects"
    lines = [f"## Memory Browse: {scope_desc}\n"]

    if not records:
        lines.append("_No memories found matching filters._")
        return "\n".join(lines)

    filters = []
    if args.wing:
        filters.append(f"wing={args.wing}")
    if args.room:
        filters.append(f"room={args.room}")
    if args.drawer:
        filters.append(f"drawer={args.drawer}")
    if args.tag:
        filters.append(f"tag={args.tag}")
    filters.append(f"confidence={args.confidence}")
    lines.append(f"_Filters: {', '.join(filters)} | {len(records)} result(s)_\n")

    # Group by room for hierarchy display
    grouped: dict[str, list] = {}
    for r in records:
        key = r.room or r.wing or "ungrouped"
        grouped.setdefault(key, []).append(r)

    for group_name, group_records in grouped.items():
        lines.append(f"### {group_name}")
        for r in group_records:
            preview = r.text[:200] + "…" if len(r.text) > 200 else r.text
            source_name = Path(r.source_uri).name if r.source_uri else "?"
            confidence_badge = f"[{r.confidence}]"
            ts = r.timestamp.strftime("%Y-%m-%d") if r.timestamp else "?"
            lines.append(f"- {confidence_badge} `{ts}` — {preview}")
            lines.append(f"  _{source_name}_")
        lines.append("")

    return "\n".join(lines)


def _infer_active_project() -> str | None:
    """Infer active project from CWD (e.g. .../myproject/...)."""
    cwd = Path.cwd()
    parts = cwd.parts
    for i, part in enumerate(parts):
        if part.lower() == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    # Fallback: check MEMORYWEAVER_PROJECT env var
    return os.environ.get("MEMORYWEAVER_PROJECT")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(run(sys.argv[1:]))
