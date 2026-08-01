"""Standing plan helpers for M9 long-horizon state."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA = "myvault-standing-plan/1"


def planning_root() -> Path:
    return Path(os.environ.get("myvault_STANDING_PLANS_ROOT", r"${HERMES_STANDING_PLANS_ROOT}"))


def lock_path(goal: str | None = None) -> Path:
    configured = os.environ.get("myvault_STANDING_PLAN_LOCK") or os.environ.get("MEMORYWEAVER_INGEST_LOCK")
    if configured:
        return Path(configured)
    name = _safe_goal(goal or "global")
    return planning_root() / name / ".standing-plan.lock"


def _safe_goal(value: str) -> str:
    goal = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    if not goal:
        raise ValueError("standing goal is required")
    return goal


def plan_path(goal: str) -> Path:
    return planning_root() / _safe_goal(goal) / "STANDING-PLAN.md"


def ledger_path(goal: str) -> Path:
    return planning_root() / _safe_goal(goal) / "standing-plan-ledger.jsonl"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_is_stale(path: Path, stale_s: float) -> bool:
    try:
        stat = path.stat()
        if time.time() - stat.st_mtime > stale_s:
            return True
        data = path.read_text(encoding="utf-8")
        match = re.search(r'"pid"\s*:\s*(\d+)', data)
        if os.name != "nt" and match and not _pid_alive(int(match.group(1))):
            return True
    except Exception:
        return True
    return False


@contextmanager
def _reap_guard(path: Path, stale_s: float):
    guard = path.with_name(path.name + ".reap")
    handle: int | None = None
    try:
        handle = os.open(str(guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - guard.stat().st_mtime > stale_s:
                guard.unlink()
                handle = os.open(str(guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                yield False
                return
        except (FileExistsError, FileNotFoundError, PermissionError, OSError):
            yield False
            return
    except OSError:
        yield False
        return
    try:
        yield True
    finally:
        if handle is not None:
            os.close(handle)
        try:
            guard.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def plan_lock(goal: str, timeout_s: float = 10.0):
    path = lock_path(goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout_s
    stale_s = float(os.environ.get("myvault_STANDING_PLAN_LOCK_STALE_S", "120"))
    handle: int | None = None
    while handle is None:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = json.dumps({"pid": os.getpid(), "created_at": datetime.now(timezone.utc).isoformat()})
            try:
                os.write(handle, payload.encode("ascii", errors="ignore"))
            except Exception:
                os.close(handle)
                handle = None
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                raise
            hold_s = float(os.environ.get("myvault_STANDING_PLAN_LOCK_HOLD_S", "0"))
            if hold_s > 0:
                time.sleep(hold_s)
        except FileExistsError:
            if _lock_is_stale(path, stale_s):
                with _reap_guard(path, stale_s) as can_reap:
                    if not can_reap:
                        time.sleep(0.05)
                        continue
                    if not _lock_is_stale(path, stale_s):
                        continue
                    try:
                        path.unlink()
                        continue
                    except FileNotFoundError:
                        continue
                    except PermissionError:
                        time.sleep(0.05)
                        continue
                    except OSError:
                        time.sleep(0.05)
                        continue
            if time.time() >= deadline:
                raise TimeoutError(f"standing plan lock busy: {path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if handle is not None:
            os.close(handle)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _safe_goal_result(goal: str) -> tuple[str | None, str | None]:
    try:
        return _safe_goal(goal), None
    except ValueError as exc:
        return None, str(exc)


def _goal_error(goal: str, *, claimed: bool | None = None, completed: bool | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "active": True,
        "goal": "",
        "error": "standing goal is required",
        "context": "Standing plan is temporarily unavailable; continue without claiming or completing a step.",
    }
    if claimed is not None:
        result["claimed"] = claimed
    if completed is not None:
        result["completed"] = completed
    return result


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.S)
    raw = match.group(1) if match else text
    return json.loads(raw)


def load_plan(path: Path) -> dict[str, Any]:
    plan = _extract_json(path.read_text(encoding="utf-8"))
    if plan.get("schema") != SCHEMA:
        raise ValueError(f"standing plan schema must be {SCHEMA}")
    if not isinstance(plan.get("steps"), list) or not plan["steps"]:
        raise ValueError("standing plan requires non-empty steps")
    return plan


def write_plan(path: Path, plan: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(plan, indent=2, ensure_ascii=True)
    path.write_text(f"# STANDING-PLAN\n\n```json\n{body}\n```\n", encoding="utf-8")


def append_ledger(goal: str, event: str, row: dict[str, Any]) -> None:
    payload = {
        "schema": "myvault-standing-plan-ledger/1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "goal": _safe_goal(goal),
        "event": event,
        **row,
    }
    path = ledger_path(goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")


def _expires_at(ttl_s: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_s)).isoformat()


def _is_expired(step: dict[str, Any]) -> bool:
    claim = step.get("claim") or {}
    expires = claim.get("expires_at")
    if not expires:
        return True
    try:
        return datetime.fromisoformat(str(expires)) <= datetime.now(timezone.utc)
    except ValueError:
        return True


def _session_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("session_id") or kwargs.get("task_id") or "unknown")


def standing_goal_from(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("standing_goal") or kwargs.get("goal") or kwargs.get("standing_plan_goal") or "")


def maybe_claim_step(kwargs: dict[str, Any]) -> dict[str, Any]:
    goal = standing_goal_from(kwargs)
    platform = str(kwargs.get("platform") or "")
    if not goal or platform not in {"gateway", "kanban"} or not bool(kwargs.get("is_first_turn")):
        return {"active": False}
    session_id = _session_id(kwargs)
    safe_goal, goal_err = _safe_goal_result(goal)
    if goal_err:
        return _goal_error(goal, claimed=False)
    path = planning_root() / str(safe_goal) / "STANDING-PLAN.md"
    try:
        with plan_lock(goal):
            plan = load_plan(path)
            ttl_s = int(plan.get("claim_ttl_s") or 3600)
            selected: dict[str, Any] | None = None
            reclaimed_from: str | None = None
            for step in plan["steps"]:
                status = str(step.get("status") or "pending")
                if status == "pending" or (status == "claimed" and _is_expired(step)):
                    if status == "claimed":
                        reclaimed_from = (step.get("claim") or {}).get("session_id")
                    step["status"] = "claimed"
                    step["claim"] = {
                        "session_id": session_id,
                        "platform": platform,
                        "claimed_at": datetime.now(timezone.utc).isoformat(),
                        "expires_at": _expires_at(ttl_s),
                    }
                    selected = step
                    break
            write_plan(path, plan)
    except Exception as exc:
        return {"active": True, "goal": safe_goal, "claimed": False, "error": str(exc), "context": "Standing plan is temporarily unavailable; continue without claiming a step."}
    if selected is None:
        return {"active": True, "goal": safe_goal, "claimed": False, "context": "Standing plan has no claimable steps."}
    row = {"session_id": session_id, "platform": platform, "step_id": selected.get("id"), "status": "claimed"}
    if reclaimed_from:
        row["reclaimed_from_session_id"] = reclaimed_from
    append_ledger(goal, "step_claimed", row)
    title = selected.get("title") or selected.get("id")
    return {
        "active": True,
        "goal": safe_goal,
        "claimed": True,
        "step_id": selected.get("id"),
        "context": f"Standing plan goal '{safe_goal}': claimed step {selected.get('id')} - {title}. Complete only this step in this session.",
    }


def maybe_complete_step(kwargs: dict[str, Any], hook: str) -> dict[str, Any]:
    goal = standing_goal_from(kwargs)
    if not goal:
        return {"active": False}
    session_id = _session_id(kwargs)
    if session_id == "unknown":
        return {"active": True, "completed": False, "reason": "missing session_id"}
    safe_goal, goal_err = _safe_goal_result(goal)
    if goal_err:
        return _goal_error(goal, completed=False)
    path = planning_root() / str(safe_goal) / "STANDING-PLAN.md"
    if not path.exists():
        return {"active": True, "completed": False, "reason": "missing plan"}
    default_completed = hook == "kanban_task_completed"
    completed_flag = bool(kwargs.get("completed", default_completed))
    completed_step: dict[str, Any] | None = None
    try:
        with plan_lock(goal):
            plan = load_plan(path)
            for step in plan["steps"]:
                claim = step.get("claim") or {}
                if step.get("status") == "claimed" and claim.get("session_id") == session_id:
                    if not completed_flag:
                        completed_step = step
                        break
                    step["status"] = "completed"
                    step["completed_at"] = datetime.now(timezone.utc).isoformat()
                    completed_step = step
                    break
            write_plan(path, plan)
    except Exception as exc:
        return {"active": True, "completed": False, "reason": "lock_or_plan_error", "error": str(exc)}
    if completed_step is None:
        return {"active": True, "completed": False, "reason": "no claimed step for session"}
    if not completed_flag:
        append_ledger(goal, "step_completion_skipped", {"session_id": session_id, "platform": kwargs.get("platform") or "", "hook": hook, "step_id": completed_step.get("id"), "status": completed_step.get("status")})
        return {"active": True, "completed": False, "step_id": completed_step.get("id"), "reason": "completed_false"}
    row = {
        "session_id": session_id,
        "platform": kwargs.get("platform") or "",
        "hook": hook,
        "step_id": completed_step.get("id"),
        "status": completed_step.get("status"),
    }
    append_ledger(goal, "step_completed", row)
    return {"active": True, "completed": True, "step_id": completed_step.get("id")}

