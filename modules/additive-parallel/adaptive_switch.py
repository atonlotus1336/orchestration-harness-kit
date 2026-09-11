"""Portable, provider-free adaptive switch state machine.

Recipients supply a closed harness registry plus signer and consumer adapters.
Installation never calls this module and therefore remains inactive.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

STATES = {"ACTIVE", "INACTIVE", "PARTIAL", "INVALID"}


class PortableSwitchError(ValueError):
    pass


def resolve_targets(registry, *, global_selector=False, harnesses=()):
    registry = tuple(registry)
    if not registry or len(registry) != len(set(registry)):
        raise PortableSwitchError("registry must be closed, nonempty, and unique")
    if global_selector and harnesses:
        raise PortableSwitchError("global and harness selectors cannot be combined")
    if not global_selector and not harnesses:
        raise PortableSwitchError("exactly one selector is required")
    requested = registry if global_selector else tuple(dict.fromkeys(harnesses))
    if set(requested) - set(registry):
        raise PortableSwitchError("selector contains an unregistered harness")
    return tuple(name for name in registry if name in requested)


def transition(registry, enabled, targets, *, turn_on):
    registry, enabled, targets = tuple(registry), set(enabled), set(targets)
    if enabled - set(registry) or targets - set(registry):
        raise PortableSwitchError("state contains an unregistered harness")
    return tuple(name for name in registry
                 if name in (enabled | targets if turn_on else enabled - targets))


def global_state(registry, enabled):
    enabled = set(enabled)
    if not enabled:
        return "INACTIVE"
    if enabled == set(registry):
        return "ACTIVE"
    return "PARTIAL"


class PortableSwitch:
    """Atomic paper-state owner; adapters retain all signing/runtime authority."""
    def __init__(self, state_root, registry, adapters):
        self.root, self.registry, self.adapters = Path(state_root), tuple(registry), adapters
        if self.root.is_symlink() or set(adapters) != set(self.registry):
            raise PortableSwitchError("state root or adapter registry is invalid")
        self.path = self.root / "adaptive-switch.json"

    def _read(self):
        if not self.path.exists():
            return {"schema_version": "1.0", "enabled_harnesses": []}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if set(value) != {"schema_version", "enabled_harnesses"} or value["schema_version"] != "1.0":
            raise PortableSwitchError("stored switch state is invalid")
        transition(self.registry, value["enabled_harnesses"], (), turn_on=True)
        return value

    def _write(self, value):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".switch-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def status(self):
        enabled = self._read()["enabled_harnesses"]
        rows = {name: (self.adapters[name].verify() if name in enabled
                       else {"state": "INACTIVE"}) for name in self.registry}
        active = [name for name in self.registry if rows[name].get("state") == "ACTIVE"]
        state = global_state(self.registry, active)
        if any(rows[name].get("state") not in {"ACTIVE", "INACTIVE"} for name in self.registry):
            state = "INVALID"
        return {"global": state, "enabled_harnesses": active, "harnesses": rows}

    def set(self, targets, *, turn_on):
        before = self._read()["enabled_harnesses"]
        after = transition(self.registry, before, targets, turn_on=turn_on)
        if tuple(before) == after:
            return {"changed": False, "after": self.status()}
        if turn_on:
            for name in targets:
                self.adapters[name].authorize()
        self._write({"schema_version": "1.0", "enabled_harnesses": list(after)})
        result = self.status()
        if turn_on and any(result["harnesses"][name].get("state") != "ACTIVE" for name in targets):
            self._write({"schema_version": "1.0", "enabled_harnesses": list(before)})
            return {"changed": False, "result": "FAILED_ROLLED_BACK", "after": self.status()}
        return {"changed": True, "result": result["global"], "after": result}
