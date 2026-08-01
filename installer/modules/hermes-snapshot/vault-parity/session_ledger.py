"""Session ledger and MemoryWeaver refresh helpers for vault-parity."""

from __future__ import annotations

import json
import os
import subprocess
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _default_data_dir() -> Path:
    if os.name == "nt":
        return Path(r"${MEMORYWEAVER_DATA_DIR}")
    return Path("${MEMORYWEAVER_DATA_DIR}")


def data_dir() -> Path:
    return Path(os.environ.get("MEMORYWEAVER_DATA_DIR") or _default_data_dir())


def ledger_path() -> Path:
    return data_dir() / "logs" / "vault-parity-ledger.jsonl"


def session_id_from(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("session_id") or kwargs.get("task_id") or "unknown")


def refresh_marker_name(session_id: str) -> str:
    digest = sha256(session_id.encode("utf-8", errors="replace")).hexdigest()
    return f"{digest}.done"


def append_session_event(hook: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    row = {
        "schema": "vault-parity-session-ledger/1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": hook,
        "session_id": session_id_from(kwargs),
        "task_id": kwargs.get("task_id") or "",
        "turn_id": kwargs.get("turn_id") or "",
        "platform": kwargs.get("platform") or "",
        "model": kwargs.get("model") or "",
        "completed": kwargs.get("completed"),
        "interrupted": kwargs.get("interrupted"),
        "reason": kwargs.get("reason") or "",
        "summary": kwargs.get("summary") or "",
        "run_id": kwargs.get("run_id"),
        "profile_name": kwargs.get("profile_name") or "",
    }
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    return row


def _run_wsl(script: str, timeout: int = 120) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["wsl", "--", "bash", "-lc", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-1000:],
            "stderr_tail": (proc.stderr or "")[-1000:],
        }
    except Exception as exc:
        return {"exit_code": 1, "error": str(exc)}


def refresh_memory_best_effort(session_id: str, hook: str) -> dict[str, Any]:
    if not session_id or session_id == "unknown":
        return {"skipped": True, "reason": "missing session_id"}
    marker_dir = data_dir() / "logs" / "vault-parity-refresh-markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / refresh_marker_name(session_id)
    if marker.exists():
        return {"skipped": True, "reason": "already refreshed for session_id"}
    try:
        marker.write_text(f"in_progress:{hook}", encoding="utf-8")
    except Exception:
        pass

    if os.name == "nt":
        script = (
            "export MEMORYWEAVER_DATA_DIR=${MEMORYWEAVER_DATA_DIR}; "
            "export MEMORYWEAVER_FTS_DB_PATH=${MEMORYWEAVER_FTS_DB_PATH}; "
            "cd ${MEMORYWEAVER_APP_DIR} && "
            "${MEMORYWEAVER_PYTHON} -m memoryweaver ingest && "
            "bash ${MEMORYWEAVER_DIGEST_HOOK}"
        )
        result = _run_wsl(script)
        return _record_refresh_result(marker, hook, result)

    env = os.environ.copy()
    env.setdefault("MEMORYWEAVER_DATA_DIR", "${MEMORYWEAVER_DATA_DIR}")
    env.setdefault("MEMORYWEAVER_FTS_DB_PATH", "${MEMORYWEAVER_FTS_DB_PATH}")
    python_bin = os.path.expandvars("${MEMORYWEAVER_PYTHON}")
    app_dir = os.path.expandvars("${MEMORYWEAVER_APP_DIR}")
    digest_hook = os.path.expandvars("${MEMORYWEAVER_DIGEST_HOOK}")
    if "$" in python_bin or "$" in app_dir or "$" in digest_hook:
        return _record_refresh_result(marker, hook, {"exit_code": 3, "error": "memoryweaver placeholders unresolved"})
    try:
        ingest = subprocess.run(
            [python_bin, "-m", "memoryweaver", "ingest"],
            cwd=app_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        digest = subprocess.run(
            ["bash", digest_hook],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        result = {
            "exit_code": digest.returncode if ingest.returncode == 0 else ingest.returncode,
            "ingest_stdout_tail": (ingest.stdout or "")[-1000:],
            "ingest_stderr_tail": (ingest.stderr or "")[-1000:],
            "digest_stdout_tail": (digest.stdout or "")[-1000:],
            "digest_stderr_tail": (digest.stderr or "")[-1000:],
        }
        return _record_refresh_result(marker, hook, result)
    except Exception as exc:
        result = {"exit_code": 1, "error": str(exc)}
        return _record_refresh_result(marker, hook, result)


def _record_refresh_result(marker: Path, hook: str, result: dict[str, Any]) -> dict[str, Any]:
    if int(result.get("exit_code") or 0) == 0:
        try:
            marker.write_text(f"ok:{hook}", encoding="utf-8")
        except Exception:
            pass
    else:
        try:
            marker.unlink(missing_ok=True)
        except Exception:
            pass
    return result


