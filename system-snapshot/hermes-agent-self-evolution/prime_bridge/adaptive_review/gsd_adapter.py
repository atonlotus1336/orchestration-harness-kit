"""Pure helpers for shadow-only GSD lifecycle hook installation and reuse."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping


class HookError(ValueError):
    pass


@dataclass(frozen=True)
class Hook:
    boundary: str
    anchor: str
    block: str

    @property
    def begin_marker(self) -> str:
        return f"<!-- BEGIN adaptive-review:gsd-shadow:{self.boundary} -->"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_hooks(config: Mapping[str, object]) -> tuple[Hook, ...]:
    if config.get("rollout_mode") != "shadow":
        raise HookError("GSD hooks may first install only in shadow mode")
    hooks = tuple(Hook(str(item["boundary"]), str(item["anchor"]), str(item["block"])) for item in config["hooks"])
    if tuple(item.boundary for item in hooks) != ("plan", "wave_close", "phase"):
        raise HookError("expected exact plan, wave_close, phase hook order")
    for hook in hooks:
        if hook.begin_marker not in hook.block or "non-authoritative" not in hook.block.lower():
            raise HookError(f"invalid shadow hook block: {hook.boundary}")
    return hooks


def install_text(original: str, hooks: Iterable[Hook]) -> tuple[str, tuple[str, ...]]:
    result = original
    installed: list[str] = []
    for hook in hooks:
        count = result.count(hook.anchor)
        if hook.begin_marker in result:
            continue
        if count != 1:
            raise HookError(f"anchor count for {hook.boundary} is {count}, expected 1")
        result = result.replace(hook.anchor, hook.anchor + "\n\n" + hook.block, 1)
        installed.append(hook.boundary)
    return result, tuple(installed)


def uncovered_phase_paths(plan_receipts: Iterable[Mapping[str, object]], phase_paths: Iterable[str]) -> tuple[str, ...]:
    covered = {path for receipt in plan_receipts for path in receipt.get("declared_paths", []) if receipt.get("verdict") == "PASS"}
    return tuple(sorted(set(phase_paths) - covered))


def dedupe_shadow_metrics(metrics: Iterable[Mapping[str, object]]) -> tuple[dict, ...]:
    seen = set()
    result = []
    for metric in metrics:
        if metric.get("authoritative"):
            continue
        key = (metric.get("content_id"), metric.get("policy_fingerprint"), metric.get("pipeline"))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(metric))
    return tuple(result)


def dedupe_parallel_feedback(
    events: Iterable[Mapping[str, object]],
) -> tuple[dict, ...]:
    """Deduplicate lifecycle feedback while unioning its declared scope coverage."""
    merged = {}
    order = []
    for event in events:
        boundary = event.get("boundary")
        if boundary not in {"plan", "wave_close", "phase"}:
            raise HookError("parallel feedback boundary is invalid")
        key = (
            boundary, event.get("content_id"), event.get("policy_fingerprint"),
            event.get("source_identity"),
        )
        if key not in merged:
            merged[key] = dict(event)
            merged[key]["declared_paths"] = sorted(set(event.get("declared_paths", ())))
            order.append(key)
        else:
            merged[key]["declared_paths"] = sorted(
                set(merged[key].get("declared_paths", ()))
                | set(event.get("declared_paths", ()))
            )
    return tuple(merged[key] for key in order)
