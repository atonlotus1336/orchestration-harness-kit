"""
python -m memoryweaver — CLI entry point.

Subcommands:
  spool     Write a MemoryEnvelope spool file (called by stop-wrap-up.py)
  ingest    Process all pending spool files
  migrate   Batch ingest existing session + decision files
  recall    Run /memory-recall query
  browse    Run /memory-browse
  status    Show ledger status (collection counts, spool queue depth)
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    # Fast-path recall/browse: pass all args after subcommand directly to the
    # sub-command's own argparser — avoids the main parser consuming flags.
    if len(sys.argv) >= 2 and sys.argv[1] in ("recall", "browse"):
        subcommand = sys.argv[1]
        subargs = sys.argv[2:]
        if subcommand == "recall":
            _cmd_recall_raw(subargs)
        else:
            _cmd_browse_raw(subargs)
        return

    parser = argparse.ArgumentParser(
        prog="memoryweaver",
        description="MemoryWeaver — Minimum-Surface Memory Ledger for Claude Code",
    )
    sub = parser.add_subparsers(dest="command")

    # sync-fts
    sub.add_parser(
        "sync-fts",
        help="Populate FTS5 index from existing ChromaDB records (one-time migration)",
    )

    # spool
    sp_spool = sub.add_parser("spool", help="Write a spool envelope")
    sp_spool.add_argument("--session", help="Path to session .md file")
    sp_spool.add_argument("--decisions", help="Path to decisions .md file")
    sp_spool.add_argument("--project", help="Project name")
    sp_spool.add_argument("--thread", help="Thread name")
    sp_spool.add_argument("--harness", help="Originating harness (claude-code|codex|hermes); inferred from env if omitted")

    # ingest
    sub.add_parser("ingest", help="Process pending spool files")

    # migrate
    sp_migrate = sub.add_parser("migrate", help="Batch ingest existing session files")
    sp_migrate.add_argument("--sessions", help="Sessions directory")
    sp_migrate.add_argument("--decisions", help="Projects directory (scans for decisions-*.md)")
    sp_migrate.add_argument("--force", action="store_true", help="Re-run even if already migrated")

    # status
    sub.add_parser("status", help="Show ledger status")

    # derive — author a derived memory citing source identity_keys
    sp_derive = sub.add_parser("derive", help="Author a derived memory citing source identity_keys")
    sp_derive.add_argument("--text", required=True)
    sp_derive.add_argument("--cites", default="", help="Comma-separated source identity_keys")
    sp_derive.add_argument("--predicate", default="cites", choices=["cites", "supersedes", "refines"],
                           help="Typed edge to the cited sources (Phase 2.5-D)")
    sp_derive.add_argument("--project")
    sp_derive.add_argument("--tags", default="", help="Comma-separated tags")

    # synthesize — auto-derive rollups from cluster strategies
    sp_synth = sub.add_parser("synthesize", help="Auto-derive rollups from clusters (tag/thread)")
    sp_synth.add_argument("--tag", action="store_true", help="Cluster by project+tag")
    sp_synth.add_argument("--thread", action="store_true", help="Cluster by thread")

    # revoke — mark a memory wrong (Phase 2.6) and quarantine its direct dependents
    sp_revoke = sub.add_parser("revoke", help="Revoke a memory by identity_key (flags direct dependents)")
    sp_revoke.add_argument("--key", required=True, help="identity_key to revoke")

    # harvest — extract [[wikilinks]]/supersedes from text into typed candidate cites (Phase 2.7)
    sp_harvest = sub.add_parser("harvest", help="Harvest [[wikilinks]]/supersedes from text into a derived memory")
    sp_harvest.add_argument("--text", required=True, help="Authored text with [[wikilinks]]/supersedes refs")
    sp_harvest.add_argument("--derived-text", dest="derived_text", help="Conclusion text (defaults to first line)")
    sp_harvest.add_argument("--project")
    sp_harvest.add_argument("--thread")
    sp_harvest.add_argument("--source-uri", dest="source_uri")

    # digest — write the recent-memory snapshot read by SessionStart hooks (Phase 4)
    sp_digest = sub.add_parser("digest", help="Write recent-digest.json (read-on-write snapshot)")
    sp_digest.add_argument("--n", type=int, default=8, help="How many recent stubs to include")

    args = parser.parse_args()

    if args.command == "sync-fts":
        _cmd_sync_fts()
    elif args.command == "spool":
        _cmd_spool(args)
    elif args.command == "ingest":
        _cmd_ingest()
    elif args.command == "migrate":
        _cmd_migrate(args)
    elif args.command == "status":
        _cmd_status()
    elif args.command == "derive":
        _cmd_derive(args)
    elif args.command == "synthesize":
        _cmd_synthesize(args)
    elif args.command == "revoke":
        _cmd_revoke(args)
    elif args.command == "harvest":
        _cmd_harvest(args)
    elif args.command == "digest":
        _cmd_digest(args)
    else:
        parser.print_help()
        sys.exit(1)


def _cmd_spool(args) -> None:
    from pathlib import Path
    from .producer import write_spool
    path = write_spool(
        session_path=Path(args.session) if args.session else None,
        decisions_path=Path(args.decisions) if args.decisions else None,
        project=args.project,
        thread=args.thread,
        harness=getattr(args, "harness", None),
    )
    print(f"Spool written: {path}")


def _cmd_ingest() -> None:
    from .producer import SpoolConsumer
    consumer = SpoolConsumer()
    result = consumer.process_pending()
    # Phase 4: refresh the read-on-write snapshot as a cheap byproduct of ingest.
    try:
        from .digest import write_recent_digest
        write_recent_digest()
    except Exception:
        pass
    print(f"Ingest complete: {result}")


def _cmd_migrate(args) -> None:
    from pathlib import Path
    from .migrate import MigrationRunner, _SCHEMA_TABLE_PATH, _SCHEMA_VERSION

    if getattr(args, "force", False) and _SCHEMA_TABLE_PATH.exists():
        content = _SCHEMA_TABLE_PATH.read_text(encoding="utf-8")
        _SCHEMA_TABLE_PATH.write_text(
            content.replace(_SCHEMA_VERSION + "\n", ""), encoding="utf-8"
        )

    runner = MigrationRunner(
        sessions_dir=Path(args.sessions) if args.sessions else None,
        projects_dir=Path(args.decisions) if args.decisions else None,
    )
    result = runner.run()
    print(f"Migration result: {result}")


def _cmd_recall_raw(argv: list) -> None:
    from .commands.recall import run
    print(run(argv))


def _cmd_browse_raw(argv: list) -> None:
    from .commands.browse import run
    print(run(argv))


def _cmd_sync_fts() -> None:
    from .store.chroma import ChromaDBAdapter
    from .store.fts import FTS5Adapter

    store = ChromaDBAdapter()
    fts = FTS5Adapter()
    result = fts.sync_from_chroma(store)
    print(f"FTS sync complete: {result}")


def _cmd_status() -> None:
    from pathlib import Path
    from .store.chroma import ChromaDBAdapter
    from .store.fts import FTS5Adapter
    from .producer import _SPOOL_DIR

    store = ChromaDBAdapter()
    fts = FTS5Adapter()
    explicit_count = store.count("explicit_memories")
    inferred_count = store.count("inferred_memories")
    fts_count = fts.count()

    pending = len(list(_SPOOL_DIR.glob("*.json"))) if _SPOOL_DIR.exists() else 0
    done = len(list(_SPOOL_DIR.glob("*.done"))) if _SPOOL_DIR.exists() else 0
    errors = len(list(_SPOOL_DIR.glob("*.error"))) if _SPOOL_DIR.exists() else 0

    fts_sync_note = ""
    chroma_total = explicit_count + inferred_count
    if fts_count == 0 and chroma_total > 0:
        fts_sync_note = "  [WARN] FTS index empty -- run: python -m memoryweaver sync-fts"
    elif fts_count < chroma_total:
        fts_sync_note = f"  [WARN] FTS ({fts_count}) < Chroma ({chroma_total}) -- run: python -m memoryweaver sync-fts"

    print(f"MemoryWeaver Status")
    print(f"  explicit_memories : {explicit_count}")
    print(f"  inferred_memories : {inferred_count}")
    print(f"  fts_index         : {fts_count}")
    print(f"  spool pending     : {pending}")
    print(f"  spool done        : {done}")
    print(f"  spool errors      : {errors}")
    if fts_sync_note:
        print(fts_sync_note)

    # Per-harness capture heartbeat (Phase 3 — makes cross-harness parity observable)
    from .producer import read_capture_status
    cap = read_capture_status()
    if cap:
        print("  capture (per harness):")
        for h in sorted(cap):
            r = cap[h] if isinstance(cap[h], dict) else {}
            print(f"    {h:<12}: last={r.get('last_ts', '?')} count={r.get('count', 0)}")


def _cmd_derive(args) -> None:
    from .derive import make_derived
    cites = [c.strip() for c in (args.cites or "").split(",") if c.strip()]
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    res = make_derived(text=args.text, cites=cites, predicate=getattr(args, "predicate", "cites"),
                       project=args.project, tags=tags)
    print(f"Derived: {res}")


def _cmd_synthesize(args) -> None:
    from .derive import synthesize
    strategies = []
    if getattr(args, "tag", False):
        strategies.append("tag")
    if getattr(args, "thread", False):
        strategies.append("thread")
    res = synthesize(strategies=strategies or None)
    print(f"Synthesize: {res}")


def _cmd_revoke(args) -> None:
    from .provenance import revoke
    from .store.chroma import ChromaDBAdapter
    res = revoke(ChromaDBAdapter(), args.key)
    print(f"Revoke: {res}")


def _cmd_harvest(args) -> None:
    from .links import harvest
    res = harvest(
        text=args.text,
        derived_text=getattr(args, "derived_text", None),
        project=args.project,
        thread=getattr(args, "thread", None),
        source_uri=getattr(args, "source_uri", None),
    )
    print(f"Harvest: {res}")


def _cmd_digest(args) -> None:
    from .digest import write_recent_digest, DIGEST_PATH
    payload = write_recent_digest(n=getattr(args, "n", 8))
    print(f"Digest: {payload['count']} stubs -> {DIGEST_PATH}")


if __name__ == "__main__":
    main()
