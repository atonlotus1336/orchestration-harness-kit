"""Provider-free portable contract for unified orchestration switches."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from adaptive_switch import PortableSwitchError, global_state

FEATURE_MODES = {
    "route": ("on", "off", "native-only", "auto", "prime", "prime-omp", "full"),
    "review": ("on", "off", "shadow", "enforce", "full"),
    "learning": ("on", "off", "observe", "promote", "full"),
    "autonomy": ("on", "off", "handoff", "unattended", "full"),
    "memory": ("on", "off", "recall", "read-write", "full"),
    "token-compute": ("on", "off", "economy", "balanced", "adaptive", "full"),
    "rag": ("on", "off", "serve", "index", "full"),
    "orca": ("on", "off", "observe", "quiesce", "supervised", "full"),
}
FEATURES = tuple(FEATURE_MODES)
HARNESS_REGISTRY = (
    "codex", "hermes", "claude-code", "moa", "cursor", "dsh", "pi",
    "opencode", "antigravity",
)


class PortableOrchestrationSwitch:
    """Own inactive-by-default recipient state; consumers retain authority."""

    def __init__(self, state_root, registry=HARNESS_REGISTRY):
        self.root, self.registry = Path(state_root), tuple(registry)
        if (not self.registry or len(self.registry) != len(set(self.registry))
                or self.root.is_symlink()):
            raise PortableSwitchError("portable orchestration state owner is invalid")
        self.path = self.root / "orchestration-switches.json"

    def _read(self):
        if not self.path.exists():
            return {harness: {feature: "off" for feature in FEATURES}
                    for harness in self.registry}
        if self.path.is_symlink() or not self.path.is_file():
            raise PortableSwitchError("stored orchestration state is invalid")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict)
                or set(value) != {"schema_version", "harnesses"}
                or value["schema_version"] not in {"1.0", "1.1"}
                or not isinstance(value["harnesses"], dict)
                or set(value["harnesses"]) != set(self.registry)):
            raise PortableSwitchError("stored orchestration state is invalid")
        matrix = {}
        for harness in self.registry:
            row = value["harnesses"][harness]
            if not isinstance(row, dict) or set(row) != set(FEATURES):
                raise PortableSwitchError("stored orchestration state is invalid")
            matrix[harness] = {}
            for feature in FEATURES:
                if row[feature] not in FEATURE_MODES[feature]:
                    raise PortableSwitchError("stored orchestration state is invalid")
                matrix[harness][feature] = row[feature]
        return matrix

    def _write_bytes(self, payload):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or (self.path.exists() and self.path.is_symlink()):
            raise PortableSwitchError("portable orchestration state path is invalid")
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".orchestration-", suffix=".tmp")
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
        if self._read() != expected:
            raise PortableSwitchError("portable orchestration state verification failed")

    def status(self, feature=None, harnesses=None):
        selected = tuple(harnesses or self.registry)
        names = (feature,) if feature else FEATURES
        if set(selected) - set(self.registry) or not selected or set(names) - set(FEATURES):
            raise PortableSwitchError("unsupported feature or harness")
        try:
            matrix = self._read()
        except Exception:
            return {"global": "INVALID", "features": {}, "consumer_required": True}
        rows, values = {}, []
        for name in names:
            modes = {harness: matrix[harness][name] for harness in selected}
            active = tuple(harness for harness, mode in modes.items() if mode != "off")
            rows[name] = {
                "global": global_state(selected, active), "harnesses": modes,
                "desired_modes": modes,
                "full_harnesses": [harness for harness, mode in modes.items() if mode == "full"],
            }
            values.extend(modes.values())
        active_cells = sum(mode != "off" for mode in values)
        overall = "INACTIVE" if active_cells == 0 else "ACTIVE" if active_cells == len(values) else "PARTIAL"
        return {"global": overall, "features": rows, "consumer_required": True, "full_is_bounded": True}

    def set(self, feature, mode, targets, *, verified_orca_off=False):
        targets = tuple(name for name in self.registry if name in set(targets))
        if not targets:
            raise PortableSwitchError("unsupported or empty harness target")
        if feature == "all":
            if mode not in {"on", "off", "full"}:
                raise PortableSwitchError("all accepts only on, off, or full")
            names = FEATURES
        elif feature in FEATURES and mode in FEATURE_MODES[feature]:
            names = (feature,)
        else:
            raise PortableSwitchError("unsupported feature mode")
        before = self._read()
        if (mode == "off" and "orca" in names
                and any(before[harness]["orca"] != "off" for harness in targets)
                and not verified_orca_off):
            raise PortableSwitchError("Orca off requires recipient-verified drain")
        after = {harness: dict(row) for harness, row in before.items()}
        changed = []
        for harness in targets:
            for name in names:
                if after[harness][name] != mode:
                    after[harness][name] = mode
                    changed.append({"harness": harness, "feature": name})
        if not changed:
            status = self.status()
            return {"changed": False, "changed_cells": [], "result": status["global"], "after": status}
        prior = self.path.read_bytes() if self.path.exists() else None
        try:
            self._write_bytes((json.dumps({
                "schema_version": "1.1", "harnesses": after,
            }, sort_keys=True, indent=2) + "\n").encode())
            self._verify(after)
        except Exception:
            self._restore(prior)
            return {"changed": False, "changed_cells": [], "result": "FAILED_ROLLED_BACK", "after": self.status()}
        status = self.status()
        return {"changed": True, "changed_cells": changed, "result": status["global"], "after": status}


def mode_for(state_path, feature, harness, registry=HARNESS_REGISTRY):
    owner = PortableOrchestrationSwitch(Path(state_path).parent, registry)
    if owner.path != Path(state_path):
        raise PortableSwitchError("orchestration state filename is invalid")
    return owner._read()[harness][feature]
