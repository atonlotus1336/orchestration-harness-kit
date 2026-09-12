"""Provider-free portable state owner for observation-only Orca adapters."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from adaptive_switch import PortableSwitchError, global_state, transition

MODE = "observation_only"


class PortableOrcaObserverSwitch:
    """Toggle a state file that recipient worker transports consume."""

    def __init__(self, state_root, registry=("codex", "hermes")):
        self.root, self.registry = Path(state_root), tuple(registry)
        if (not self.registry or len(self.registry) != len(set(self.registry))
                or self.root.is_symlink()):
            raise PortableSwitchError("Orca observer state owner is invalid")
        self.path = self.root / "orca-observer.json"

    def _read(self):
        if not self.path.exists():
            return ()
        if self.path.is_symlink() or not self.path.is_file():
            raise PortableSwitchError("stored Orca observer state is invalid")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict)
                or set(value) != {"schema_version", "mode", "enabled_harnesses"}
                or value["schema_version"] != "1.0" or value["mode"] != MODE
                or not isinstance(value["enabled_harnesses"], list)):
            raise PortableSwitchError("stored Orca observer state is invalid")
        enabled = tuple(value["enabled_harnesses"])
        transition(self.registry, enabled, (), turn_on=True)
        return tuple(name for name in self.registry if name in enabled)

    def _write_bytes(self, payload):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or (self.path.exists() and self.path.is_symlink()):
            raise PortableSwitchError("Orca observer state path is invalid")
        fd, temporary = tempfile.mkstemp(
            dir=self.root, prefix=".orca-observer-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _restore(self, prior):
        if prior is None:
            self.path.unlink(missing_ok=True)
        else:
            self._write_bytes(prior)

    def _verify(self, expected):
        if self._read() != tuple(expected):
            raise PortableSwitchError("Orca observer state verification failed")

    def status(self):
        try:
            enabled = self._read()
        except Exception:
            return {"global": "INVALID", "enabled_harnesses": [],
                    "mode": MODE, "consumer_required": True}
        return {
            "global": global_state(self.registry, enabled),
            "enabled_harnesses": list(enabled), "mode": MODE,
            "consumer_required": True,
            "authority": {
                "observe_only": True, "launch": False, "command": False,
                "approve": False, "review": False, "complete": False,
                "poll": False,
            },
        }

    def set(self, targets, *, turn_on):
        before = self._read()
        after = transition(
            self.registry, before, targets, turn_on=turn_on)
        changed = tuple(
            name for name in targets if (name in before) != (name in after))
        if not changed:
            status = self.status()
            return {"changed": False, "changed_harnesses": [],
                    "result": status["global"], "after": status}
        prior = self.path.read_bytes() if self.path.exists() else None
        value = {"schema_version": "1.0", "mode": MODE,
                 "enabled_harnesses": list(after)}
        try:
            self._write_bytes(
                (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
            self._verify(after)
        except Exception:
            self._restore(prior)
            return {"changed": False, "changed_harnesses": [],
                    "result": "FAILED_ROLLED_BACK", "after": self.status()}
        status = self.status()
        return {"changed": True, "changed_harnesses": list(changed),
                "result": status["global"], "after": status}


def enabled_harnesses(state_path, registry=("codex", "hermes")):
    """Recipient adapter seam: invalid state raises and must block observation."""
    path = Path(state_path)
    owner = PortableOrcaObserverSwitch(path.parent, registry)
    if owner.path != path:
        raise PortableSwitchError("Orca observer state filename is invalid")
    return frozenset(owner._read())
