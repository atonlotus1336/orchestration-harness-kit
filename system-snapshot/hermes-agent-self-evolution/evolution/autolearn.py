"""Unattended proposal runner for Hermes Agent self-evolution.

The runner watches session history, mines examples, and writes evolved skill
proposals under output/. It intentionally does not overwrite live skills.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HERMES_REPO = Path(r"__WINDOWS_WORKSPACE__\hermes-agent")
DEFAULT_HERMES_HOME = Path.home() / ".hermes" / "profiles" / "home"
STATE_PATH = ROOT / "output" / "autolearn" / "state.json"
LOG_DIR = ROOT / "output" / "autolearn" / "logs"
DEFAULT_SKILLS = (
    "github-code-review",
    "github-pr-workflow",
    "codebase-inspection",
    "test-driven-development",
    "systematic-debugging",
    "plan",
    "writing-plans",
)


def _split_skills(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_SKILLS)
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _session_files(hermes_home: Path) -> list[Path]:
    sessions = hermes_home / "sessions"
    if not sessions.exists():
        return []
    return sorted(sessions.glob("*.json"), key=lambda p: p.stat().st_mtime)


def _new_session_count(files: list[Path], state: dict) -> int:
    last_mtime = float(state.get("last_session_mtime", 0))
    return sum(1 for path in files if path.stat().st_mtime > last_mtime)


def _probe_gateway(base_url: str) -> bool:
    root = base_url.removesuffix("/v1").rstrip("/")
    request = Request(f"{root}/health", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def _run_skill(skill: str, args: argparse.Namespace, env: dict[str, str]) -> tuple[int, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{timestamp}-{skill}.log"

    command = [
        sys.executable,
        "-m",
        "evolution.skills.evolve_skill",
        "--skill",
        skill,
        "--iterations",
        str(args.iterations),
        "--eval-source",
        args.eval_source,
        "--optimizer-model",
        args.optimizer_model,
        "--eval-model",
        args.eval_model,
        "--hermes-repo",
        str(args.hermes_repo),
    ]
    if args.run_tests:
        command.append("--run-tests")
    if args.dry_run:
        command.append("--dry-run")

    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        log_file.write("$ " + " ".join(command) + "\n\n")
        proc = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return proc.returncode, log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run automatic Hermes self-evolution proposals.")
    parser.add_argument("--skills", default=os.getenv("HERMES_AUTOLEARN_SKILLS"))
    parser.add_argument("--iterations", type=int, default=int(os.getenv("HERMES_AUTOLEARN_ITERATIONS", "3")))
    parser.add_argument("--eval-source", choices=("sessiondb", "synthetic", "golden"), default=os.getenv("HERMES_AUTOLEARN_EVAL_SOURCE", "sessiondb"))
    parser.add_argument("--optimizer-model", default=os.getenv("HERMES_AUTOLEARN_OPTIMIZER_MODEL", "openai/hermes-agent"))
    parser.add_argument("--eval-model", default=os.getenv("HERMES_AUTOLEARN_EVAL_MODEL", "openai/hermes-agent"))
    parser.add_argument("--hermes-repo", type=Path, default=Path(os.getenv("HERMES_AGENT_REPO", str(DEFAULT_HERMES_REPO))))
    parser.add_argument("--hermes-home", type=Path, default=Path(os.getenv("HERMES_HOME", str(DEFAULT_HERMES_HOME))))
    parser.add_argument("--min-new-sessions", type=int, default=int(os.getenv("HERMES_AUTOLEARN_MIN_NEW_SESSIONS", "1")))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--gateway-url", default=os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or "http://127.0.0.1:8642/v1")
    args = parser.parse_args()

    state = _load_state()
    sessions = _session_files(args.hermes_home)
    new_sessions = _new_session_count(sessions, state)

    if not args.force and new_sessions < args.min_new_sessions:
        print(f"[SILENT] autolearn skipped: {new_sessions} new session(s), threshold {args.min_new_sessions}.")
        return 0

    env = os.environ.copy()
    env["HERMES_AGENT_REPO"] = str(args.hermes_repo)
    env["HERMES_HOME"] = str(args.hermes_home)
    env.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
    env.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
    env.setdefault("HUGGINGFACE_HUB_CACHE", str(ROOT / ".cache" / "huggingface" / "hub"))
    env.setdefault("TRANSFORMERS_CACHE", str(ROOT / ".cache" / "huggingface" / "transformers"))
    env.setdefault("DSPY_CACHEDIR", str(ROOT / ".cache" / "dspy"))
    env.setdefault("TMP", str(ROOT / ".cache" / "tmp"))
    env.setdefault("TEMP", env["TMP"])
    env.setdefault("OPENAI_BASE_URL", args.gateway_url)
    env.setdefault("OPENAI_API_BASE", args.gateway_url)
    env.setdefault("OPENAI_API_KEY", "hermes-local-gateway")

    for path in (Path(env["XDG_CACHE_HOME"]), Path(env["HF_HOME"]), Path(env["HUGGINGFACE_HUB_CACHE"]), Path(env["TRANSFORMERS_CACHE"]), Path(env["DSPY_CACHEDIR"]), Path(env["TMP"])):
        path.mkdir(parents=True, exist_ok=True)

    if args.optimizer_model.startswith("openai/hermes-agent") or args.eval_model.startswith("openai/hermes-agent"):
        if not _probe_gateway(args.gateway_url):
            print(f"autolearn blocked: Hermes API gateway is not reachable at {args.gateway_url}.")
            print("Start it with API_SERVER_ENABLED=1, or use run-autolearn.cmd which starts it first.")
            return 2

    skills = _split_skills(args.skills)
    print(f"autolearn: {new_sessions} new session(s), {len(skills)} skill(s), eval={args.eval_source}")

    results = []
    for skill in skills:
        code, log_path = _run_skill(skill, args, env)
        results.append({"skill": skill, "code": code, "log": str(log_path)})
        status = "ok" if code == 0 else f"failed:{code}"
        print(f"  {skill}: {status} ({log_path})")

    if sessions:
        state["last_session_mtime"] = max(path.stat().st_mtime for path in sessions)
    state["last_run_at"] = datetime.now().isoformat(timespec="seconds")
    state["last_results"] = results
    _save_state(state)

    return 1 if any(item["code"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
