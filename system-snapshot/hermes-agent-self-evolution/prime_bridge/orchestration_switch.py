"""Transactional control plane for optional orchestration subsystems."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .adaptive_switch import _StateLock
from .harness_registry import UNIVERSAL_HARNESS_REGISTRY as HARNESS_REGISTRY

FEATURE_MODES = {
    "route": ("on", "off", "native-only", "auto", "prime", "prime-omp", "full"),
    "review": ("on", "off", "shadow", "enforce", "full"),
    "learning": ("on", "off", "observe", "promote", "full"),
    "autonomy": ("on", "off", "handoff", "unattended", "full"),
    "memory": ("on", "off", "recall", "read-write", "full"),
    "token-compute": ("on", "off", "economy", "balanced", "adaptive", "full"),
    "rag": ("on", "off", "serve", "index", "full"),
    "orca": ("on", "off", "observe", "quiesce", "supervised", "full"),
    "herdr-review": ("off", "observe", "supervised", "full"),
}
FEATURES = tuple(FEATURE_MODES)
LEGACY_HARNESSES = frozenset({"codex", "hermes"})
LEGACY_FEATURES = frozenset(set(FEATURES) - {"orca", "herdr-review"})
PRE_HERDR_FEATURES = frozenset(set(FEATURES) - {"herdr-review"})
ALL_MODES = ("on", "off", "full")
FULL_EFFECTIVE_MODES = {
    "route": "prime-omp",
    "review": "enforce",
    "learning": "promote",
    "autonomy": "unattended",
    "memory": "read-write",
    "token-compute": "full",
    "rag": "index",
    "orca": "full",
    "herdr-review": "full",
}


class SwitchControlError(ValueError):
    pass


def default_state_path() -> Path:
    override = os.environ.get("LAPART_ORCHESTRATION_SWITCH_STATE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / ".state" / "orchestration-controls" / "orchestration-switches.json"


def _default_matrix() -> dict[str, dict[str, str]]:
    return {
        harness: {
            feature: ("observe" if feature == "orca" else "off" if feature == "herdr-review" else "on")
            for feature in FEATURES
        }
        for harness in HARNESS_REGISTRY
    }


def _validate(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "harnesses", "updated_at"
    } or value["schema_version"] not in {"1.0", "1.1"}:
        raise SwitchControlError("stored orchestration switch state is invalid")
    harnesses = value["harnesses"]
    harness_keys = set(harnesses) if isinstance(harnesses, dict) else set()
    if harness_keys not in {LEGACY_HARNESSES, frozenset(HARNESS_REGISTRY)}:
        raise SwitchControlError("stored orchestration switch state is invalid")
    defaults = _default_matrix()
    normalized = {}
    for harness in HARNESS_REGISTRY:
        modes = harnesses.get(harness, {})
        mode_keys = set(modes) if isinstance(modes, dict) else set()
        if mode_keys not in {LEGACY_FEATURES, PRE_HERDR_FEATURES, frozenset(FEATURES)}:
            raise SwitchControlError("stored orchestration switch state is invalid")
        normalized[harness] = {}
        for feature in FEATURES:
            mode = modes.get(feature, defaults[harness][feature])
            if mode not in FEATURE_MODES[feature]:
                raise SwitchControlError("stored orchestration switch state is invalid")
            normalized[harness][feature] = mode
    if not isinstance(value["updated_at"], str) or not value["updated_at"]:
        raise SwitchControlError("stored orchestration switch state is invalid")
    return normalized


def load_matrix(path: Path | str | None = None) -> dict[str, dict[str, str]]:
    selected = Path(path) if path is not None else default_state_path()
    if not selected.exists():
        return _default_matrix()
    if selected.is_symlink() or not selected.is_file():
        raise SwitchControlError("stored orchestration switch state is invalid")
    try:
        return _validate(json.loads(selected.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SwitchControlError("stored orchestration switch state is invalid") from exc


def load_mode(feature: str, harness: str, path: Path | str | None = None) -> str:
    if feature not in FEATURES or harness not in HARNESS_REGISTRY:
        raise SwitchControlError("unsupported feature or harness")
    return load_matrix(path)[harness][feature]


def permits(feature: str, harness: str, path: Path | str | None = None) -> bool:
    """Return False only for explicit off; invalid state raises and fails closed."""
    return load_mode(feature, harness, path) != "off"


def full_requested(feature: str, harness: str, path: Path | str | None = None) -> bool:
    return load_mode(feature, harness, path) == "full"


def effective_mode(feature: str, harness: str,
                   path: Path | str | None = None) -> str:
    """Resolve the convenience aliases without replacing owner policy."""
    mode = load_mode(feature, harness, path)
    if mode == "full":
        return FULL_EFFECTIVE_MODES[feature]
    if mode == "on":
        return "owner"
    return mode


def operation_permitted(feature: str, harness: str, operation: str,
                        path: Path | str | None = None) -> bool:
    """Enforce switch intent; subsystem policies still make the final decision."""
    mode = effective_mode(feature, harness, path)
    if mode == "off":
        return False
    if mode == "owner":
        return True
    operations = {
        "route": {
            "native-only": {"native"}, "auto": {"native", "prime", "omp"},
            "prime": {"native", "prime"}, "prime-omp": {"native", "prime", "omp"},
        },
        "review": {
            "shadow": {"evaluate", "advisory"}, "enforce": {"evaluate", "advisory", "enforce"},
        },
        "learning": {
            "observe": {"record"}, "promote": {"record", "propose", "promote"},
        },
        "autonomy": {
            "handoff": {"handoff"}, "unattended": {"handoff", "unattended"},
        },
        "memory": {
            "recall": {"recall"}, "read-write": {"recall", "write"},
        },
        "token-compute": {
            "economy": {"budget", "account", "economy"},
            "balanced": {"budget", "account", "economy", "balanced"},
            "adaptive": {"budget", "account", "economy", "balanced", "adaptive"},
            "full": {"budget", "account", "economy", "balanced", "adaptive", "full"},
        },
        "rag": {
            "serve": {"query"}, "index": {"query", "index"},
        },
        "herdr-review": {
            "observe": {"observe", "probe"},
            "supervised": {"observe", "probe", "start", "status", "reconcile"},
            "full": {"observe", "probe", "start", "status", "reconcile"},
        },
        "orca": {
            "observe": {"observe"},
            "quiesce": {
                "observe", "probe", "show", "read", "check", "stop",
                "abandon", "release", "retain", "task-list", "gate-list",
                "inbox",
            },
            "supervised": {
                "observe", "probe", "start", "show", "read", "send",
                "check", "stop", "abandon", "release", "retain", "retry",
            },
            "full": {
                "observe", "probe", "start", "show", "read", "send",
                "check", "stop", "abandon", "release", "retain", "retry",
                "gate-create", "gate-resolve", "gate-list", "remote",
                "task-create", "task-list", "task-update", "reply", "inbox",
            },
        },
    }
    return operation in operations[feature].get(mode, set())


def _global_state(values) -> str:
    enabled = tuple(value != "off" for value in values)
    if all(enabled):
        return "ACTIVE"
    if not any(enabled):
        return "INACTIVE"
    return "PARTIAL"


class OrchestrationSwitch:
    """Own switch intent while leaving execution authority with each subsystem."""

    def __init__(self, state_root: Path | str, *, now=None):
        self.root = Path(state_root)
        if self.root.is_symlink():
            raise SwitchControlError("symlinked state roots are not allowed")
        self.path = self.root / "orchestration-switches.json"
        self.lock = self.root / ".orchestration-switches.lock"
        self.receipts = self.root / "orchestration-switch-receipts"
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _read(self):
        source = "explicit" if self.path.exists() else "legacy_default"
        return load_matrix(self.path), source

    def _write_bytes(self, payload: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or (self.path.exists() and self.path.is_symlink()):
            raise SwitchControlError("orchestration switch path is invalid")
        fd, temporary = tempfile.mkstemp(
            dir=self.root, prefix=".orchestration-switch-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _restore(self, prior: bytes | None) -> None:
        if prior is None:
            self.path.unlink(missing_ok=True)
        else:
            self._write_bytes(prior)

    def _verify(self, expected) -> None:
        if load_matrix(self.path) != expected:
            raise SwitchControlError("orchestration switch verification failed")

    def _receipt(self, feature, mode, targets, result) -> str:
        value = {
            "schema_version": "1.1", "feature": feature, "mode": mode,
            "targets": list(targets), "result": result,
            "created_at": self.now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        identity = hashlib.sha256(encoded).hexdigest()
        self.receipts.mkdir(parents=True, exist_ok=True)
        destination = self.receipts / f"{identity}.json"
        if not destination.exists():
            destination.write_bytes(encoded)
        return identity

    def status(self, *, feature: str | None = None, harnesses=None) -> dict:
        if feature is not None and feature not in FEATURES:
            raise SwitchControlError("unsupported feature")
        selected_harnesses = tuple(harnesses or HARNESS_REGISTRY)
        if not selected_harnesses or set(selected_harnesses) - set(HARNESS_REGISTRY):
            raise SwitchControlError("unsupported harness")
        try:
            matrix, source = self._read()
        except SwitchControlError:
            return {
                "schema_version": "1.0", "global": "INVALID",
                "source": "invalid", "features": {},
            }
        selected_features = (feature,) if feature else FEATURES
        rows = {}
        for name in selected_features:
            modes = {harness: matrix[harness][name] for harness in selected_harnesses}
            rows[name] = {
                "global": _global_state(modes.values()),
                "harnesses": modes,
                "desired_modes": modes,
                "effective_modes": {
                    harness: effective_mode(name, harness, self.path)
                    for harness in selected_harnesses
                },
                "full_harnesses": [
                    harness for harness, mode in modes.items() if mode == "full"
                ],
            }
        return {
            "schema_version": "1.0",
            "global": _global_state(
                mode for harness in selected_harnesses
                for mode in (
                    matrix[harness][name] for name in selected_features
                    if name != "herdr-review" or feature == "herdr-review"
                )
            ),
            "source": source,
            "selected_harnesses": list(selected_harnesses),
            "features": rows,
            "full_is_bounded": True,
            "non_disableable_controls": [
                "approvals", "budgets", "evidence", "goalposts",
                "rollback", "sandbox",
            ],
        }

    def set(self, feature: str, mode: str, targets, *,
            verified_orca_off: bool = False) -> dict:
        targets = tuple(name for name in HARNESS_REGISTRY if name in set(targets))
        if not targets:
            raise SwitchControlError("unsupported or empty harness target")
        if feature == "all":
            if mode not in ALL_MODES:
                raise SwitchControlError("all accepts only on, off, or full")
            selected_features = FEATURES
        elif feature in FEATURES:
            if mode not in FEATURE_MODES[feature]:
                raise SwitchControlError("unsupported feature mode")
            selected_features = (feature,)
        else:
            raise SwitchControlError("unsupported feature")
        with _StateLock(self.lock):
            try:
                before, source = self._read()
            except SwitchControlError:
                return {"changed": False, "changed_cells": [],
                        "result": "INVALID", "after": self.status()}
            if (mode == "off" and "orca" in selected_features
                    and any(before[harness]["orca"] != "off" for harness in targets)
                    and not verified_orca_off):
                raise SwitchControlError("Orca off requires a verified worker drain")
            after = {harness: dict(values) for harness, values in before.items()}
            changed = []
            for harness in targets:
                for name in selected_features:
                    # "all on" preserves opt-in evaluator transports as off.
                    if feature == "all" and mode == "on" and name == "herdr-review":
                        continue
                    if after[harness][name] != mode:
                        after[harness][name] = mode
                        changed.append({"harness": harness, "feature": name})
            before_status = self.status()
            if not changed and source == "explicit":
                return {"changed": False, "changed_cells": [],
                        "result": before_status["global"],
                        "after": before_status, "receipt": None}
            prior = self.path.read_bytes() if self.path.exists() else None
            value = {
                "schema_version": "1.1", "harnesses": after,
                "updated_at": self.now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            try:
                self._write_bytes(
                    (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
                self._verify(after)
                final = self.status()
                receipt = self._receipt(
                    feature, mode, targets, final["global"])
            except Exception:
                self._restore(prior)
                return {
                    "changed": False, "changed_cells": [],
                    "result": "FAILED_ROLLED_BACK", "after": self.status(),
                    "receipt": None,
                }
            return {
                "changed": True, "changed_cells": changed,
                "result": final["global"], "after": final, "receipt": receipt,
            }
