"""Orchestration parity lifecycle plugin.

M5 responsibilities:
- inject the MemoryWeaver digest on the first turn of a session;
- inject a RED notice instead of stale memory when the freshness beacon is stale;
- append session/kanban lifecycle rows keyed by session_id;
- refresh MemoryWeaver ingest/digest state best-effort at session boundaries.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

try:
    from .digest import first_turn_context
    from .routing import MeteredRouteRefusal, route_hint
    from .session_ledger import append_session_event, refresh_memory_best_effort
    from .standing_plan import maybe_claim_step, maybe_complete_step
except ImportError:
    _ROOT = Path(__file__).resolve().parent

    def _load_local(name: str):
        spec = importlib.util.spec_from_file_location(name, _ROOT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    _digest = _load_local("digest")
    _routing = _load_local("routing")
    _session_ledger = _load_local("session_ledger")
    first_turn_context = _digest.first_turn_context
    MeteredRouteRefusal = _routing.MeteredRouteRefusal
    route_hint = _routing.route_hint
    append_session_event = _session_ledger.append_session_event
    refresh_memory_best_effort = _session_ledger.refresh_memory_best_effort
    _standing_plan = _load_local("standing_plan")
    maybe_claim_step = _standing_plan.maybe_claim_step
    maybe_complete_step = _standing_plan.maybe_complete_step


def pre_llm_call(ctx: Any = None, **kwargs: Any) -> dict[str, Any]:
    try:
        hint = route_hint(kwargs)
    except MeteredRouteRefusal as exc:
        return {"refusal": str(exc), "route_hint": {"outcome": "REFUSAL", "alias": exc.alias}}
    standing = maybe_claim_step(kwargs)
    result = first_turn_context(kwargs)
    if standing.get("claimed"):
        if result:
            result["context"] = (result.get("context") or "") + "\n\n" + standing["context"]
        else:
            result = {"context": standing["context"]}
    if result:
        result["route_hint"] = hint
        if standing.get("active"):
            result["standing_plan"] = standing
        return result
    response = {"route_hint": hint}
    if standing.get("active"):
        response["standing_plan"] = standing
    return response


def on_session_end(ctx: Any = None, **kwargs: Any) -> dict[str, Any]:
    row = append_session_event("on_session_end", kwargs)
    standing = maybe_complete_step(kwargs, "on_session_end")
    refresh = refresh_memory_best_effort(row["session_id"], "on_session_end")
    return {"vault_parity": row, "memory_refresh": refresh, "standing_plan": standing}


def on_session_finalize(ctx: Any = None, **kwargs: Any) -> dict[str, Any]:
    row = append_session_event("on_session_finalize", kwargs)
    standing = maybe_complete_step(kwargs, "on_session_finalize")
    refresh = refresh_memory_best_effort(row["session_id"], "on_session_finalize")
    return {"vault_parity": row, "memory_refresh": refresh, "standing_plan": standing}


def kanban_task_completed(ctx: Any = None, **kwargs: Any) -> dict[str, Any]:
    row = append_session_event("kanban_task_completed", kwargs)
    standing = maybe_complete_step(kwargs, "kanban_task_completed")
    return {"vault_parity": row, "standing_plan": standing}


def register(ctx: Any) -> None:
    for name, hook in (
        ("pre_llm_call", pre_llm_call),
        ("on_session_end", on_session_end),
        ("on_session_finalize", on_session_finalize),
        ("kanban_task_completed", kanban_task_completed),
    ):
        ctx.register_hook(name, hook)

