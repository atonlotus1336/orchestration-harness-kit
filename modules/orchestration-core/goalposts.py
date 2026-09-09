#!/usr/bin/env python3
"""Goalpost checkpoint adapter for Codex /goal, Hermes and GSD loops.

Exit 0: continue/complete; 10: evaluation due; 20: iterate; 30: checkpoint;
2: invalid contract or evidence. This command never starts an agent.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
sys.path.insert(0, str(ROOT))
from lapart_core.goalposts import (GoalpostError, create, evaluate, fingerprint,
                                    local_file, locked_state, markdown, record_work, save, status)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state", default=".goalposts/state.json")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--contract", type=Path, required=True)
    for name in ("status", "fingerprint"):
        sub.add_parser(name)
    work = sub.add_parser("record-work")
    work.add_argument("--event-id", required=True)
    work.add_argument("--active-seconds", type=float, required=True)
    run = sub.add_parser("run-work")
    run.add_argument("--event-id", required=True)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    judge = sub.add_parser("evaluate")
    judge.add_argument("--observations", type=Path, required=True)
    judge.add_argument("--milestone", action="store_true")
    judge.add_argument("--completion", action="store_true")
    args = parser.parse_args(argv)
    root = args.workspace.resolve(strict=True)
    try:
        path = local_file(root, args.state)
        with locked_state(path):
            if args.command == "init":
                if path.exists():
                    raise GoalpostError("goal already exists; choose a new state path for an explicitly revised goal")
                state = create(json.loads(args.contract.read_text(encoding="utf-8")))
                fingerprint(root, state["contract"]["inputs"])
                save(path, state)
                result = status(state)
            else:
                state = json.loads(path.read_text(encoding="utf-8"))
                if args.command == "fingerprint":
                    print(fingerprint(root, state["contract"]["inputs"]))
                    return 0
                if args.command == "record-work":
                    record_work(state, args.event_id, args.active_seconds)
                    save(path, state)
                    result = status(state)
                elif args.command == "run-work":
                    result = status(state)
                    if result["decision"] != "continue":
                        print(json.dumps(result, indent=2))
                        return {"evaluate":10, "checkpoint":30, "complete":0}[result["decision"]]
                    if args.event_id in state["work_events"]:
                        raise GoalpostError("work id already executed; refusing duplicate side effects")
                    command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
                    if not command:
                        raise GoalpostError("run-work requires an explicitly authorized command")
                    # Reserve the id BEFORE execution; a crash cannot silently replay it.
                    state["work_events"][args.event_id] = None
                    save(path, state)
                    started = time.monotonic()
                    remaining = state["contract"]["max_wall_seconds"] - (time.time() - state["created_at"])
                    try:
                        done = subprocess.run(command, cwd=root, timeout=max(0.001, remaining))
                        work_exit = done.returncode
                    except OSError:
                        work_exit = 126
                    except subprocess.TimeoutExpired:
                        work_exit = 124
                    except BaseException:
                        state["pending_evaluation"] = "work command interrupted"
                        raise
                    finally:
                        duration = time.monotonic() - started
                        state["work_events"][args.event_id] = duration
                        state["active_seconds"] += duration
                        save(path, state)
                    result = status(state)
                    result["work_exit"] = work_exit
                    if work_exit != 0:
                        state["pending_evaluation"] = "work command failed"
                        save(path, state)
                        result = status(state)
                        result["work_exit"] = work_exit
                elif args.command == "evaluate":
                    observations = json.loads(args.observations.read_text(encoding="utf-8"))
                    result = evaluate(state, observations, root, milestone=args.milestone, completion=args.completion)
                    save(path, state)
                    report_path = path.with_suffix(".md")
                    report_path.write_text(markdown(result), encoding="utf-8")
                    result["report_path"] = str(report_path)
                else:
                    result = status(state)
            print(json.dumps(result, indent=2))
            return {"continue":0, "complete":0, "evaluate":10, "iterate":20, "checkpoint":30}[result["decision"]]
    except (GoalpostError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"decision":"invalid", "error":str(exc)}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
