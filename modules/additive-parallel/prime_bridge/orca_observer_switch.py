"""Compatibility facade over the canonical unified Orca control.

No independent observer state is written.  Existing callers retain their
on/off grammar while all authority is stored in orchestration-switches.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from .harness_registry import UNIVERSAL_HARNESS_REGISTRY as HARNESS_REGISTRY
from .orca_control import OrcaControl
from .orchestration_switch import (
    OrchestrationSwitch,
    SwitchControlError,
    default_state_path as unified_state_path,
    load_matrix,
)


MODE = "unified_orca"


class OrcaSwitchError(ValueError):
    pass


def default_state_path() -> Path:
    return unified_state_path()


def load_enabled_harnesses(path: Path | str | None = None) -> frozenset[str]:
    try:
        matrix = load_matrix(path)
    except SwitchControlError as exc:
        raise OrcaSwitchError("stored Orca state is invalid") from exc
    return frozenset(
        harness for harness in HARNESS_REGISTRY
        if matrix[harness]["orca"] != "off"
    )


def migrate_legacy_state(state_root: Path | str) -> bool:
    """Import a colocated v1 observer file only when unified state is absent."""
    root = Path(state_root)
    unified = root / "orchestration-switches.json"
    legacy = root / "orca-observer.json"
    if unified.exists() or not legacy.is_file() or legacy.is_symlink():
        return False
    try:
        value = json.loads(legacy.read_text(encoding="utf-8"))
        enabled = value["enabled_harnesses"]
        valid = (
            isinstance(value, dict)
            and value.get("schema_version") == "1.0"
            and value.get("mode") == "observation_only"
            and isinstance(enabled, list)
            and len(enabled) == len(set(enabled))
            and not (set(enabled) - set(HARNESS_REGISTRY))
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OrcaSwitchError("stored legacy Orca observer state is invalid") from exc
    if not valid:
        raise OrcaSwitchError("stored legacy Orca observer state is invalid")
    owner = OrchestrationSwitch(root)
    owner.set("orca", "observe", HARNESS_REGISTRY)
    disabled = tuple(name for name in HARNESS_REGISTRY if name not in enabled)
    if disabled:
        owner.set("orca", "off", disabled, verified_orca_off=True)
    return True


class OrcaObserverSwitch:
    """Deprecated observer grammar backed by the unified Orca switch."""

    def __init__(self, state_root: Path | str, *, now=None):
        self.root = Path(state_root)
        try:
            migrate_legacy_state(self.root)
            self.owner = OrchestrationSwitch(self.root, now=now)
        except (OSError, SwitchControlError) as exc:
            raise OrcaSwitchError(str(exc)) from exc
        self.path = self.owner.path

    def status(self, targets=None) -> dict:
        selected = tuple(targets or HARNESS_REGISTRY)
        try:
            value = self.owner.status(feature="orca", harnesses=selected)
        except SwitchControlError as exc:
            raise OrcaSwitchError(str(exc)) from exc
        row = value["features"]["orca"]
        return {
            "schema_version": "1.1", "mode": MODE,
            "global": row["global"],
            "enabled_harnesses": [
                name for name, mode in row["harnesses"].items()
                if mode != "off"
            ],
            "harnesses": {
                name: {
                    "state": "INACTIVE" if mode == "off" else "ACTIVE",
                    "mode": mode,
                }
                for name, mode in row["harnesses"].items()
            },
            "source": value["source"], "selected": list(selected),
            "authority": {
                "observe_only": all(
                    mode in {"observe", "off"} for mode in row["harnesses"].values()
                ),
                "canonical_control": "orchestration-switches.json",
            },
        }

    def set(self, targets, *, turn_on: bool) -> dict:
        selected = tuple(targets)
        try:
            if turn_on:
                result = self.owner.set("orca", "observe", selected)
            else:
                modes = self.owner.status(feature="orca", harnesses=selected)["features"]["orca"]["harnesses"]
                if any(mode not in {"observe", "off"} for mode in modes.values()):
                    return {
                        "changed": False, "changed_harnesses": [],
                        "result": "DRAIN_REQUIRED", "after": self.status(selected),
                    }
                result = self.owner.set(
                    "orca", "off", selected, verified_orca_off=True,
                )
        except SwitchControlError as exc:
            raise OrcaSwitchError(str(exc)) from exc
        return {
            "changed": result.get("changed", False),
            "changed_harnesses": [
                cell["harness"] for cell in result.get("changed_cells", [])
            ],
            "result": self.status()["global"],
            "after": self.status(selected), "receipt": result.get("receipt"),
        }


__all__ = [
    "MODE", "OrcaObserverSwitch", "OrcaSwitchError", "default_state_path",
    "load_enabled_harnesses", "migrate_legacy_state",
]
