"""Portable transactional adaptive switch state machine.

Recipients supply a closed harness registry and adapters that retain all
signing, process, and runtime authority. Installation never calls this module
and therefore remains inactive.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import tempfile

STATES = {"ACTIVE", "INACTIVE", "PARTIAL", "INVALID"}
ADAPTER_METHODS = ("prepare", "commit", "rollback", "finalize", "verify")
ADAPTER_RESULTS = {
    "prepare": "PREPARED",
    "commit": "COMMITTED",
    "rollback": "ROLLED_BACK",
    "finalize": "FINALIZED",
}


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
    registry, enabled_values, targets = tuple(registry), tuple(enabled), set(targets)
    if len(enabled_values) != len(set(enabled_values)):
        raise PortableSwitchError("state contains duplicate harnesses")
    enabled = set(enabled_values)
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
    """Coordinate recipient adapters and paper state as one transaction."""

    def __init__(self, state_root, registry, adapters):
        self.root, self.registry, self.adapters = Path(state_root), tuple(registry), adapters
        if not self.registry or len(self.registry) != len(set(self.registry)):
            raise PortableSwitchError("registry must be closed, nonempty, and unique")
        if self.root.is_symlink() or set(adapters) != set(self.registry):
            raise PortableSwitchError("state root or adapter registry is invalid")
        for name in self.registry:
            if any(not callable(getattr(adapters[name], method, None))
                   for method in ADAPTER_METHODS):
                raise PortableSwitchError(f"adapter contract is incomplete: {name}")
        self.path = self.root / "adaptive-switch.json"

    def _read(self):
        if not self.path.exists():
            return {"schema_version": "1.0", "enabled_harnesses": []}
        if self.path.is_symlink() or not self.path.is_file():
            raise PortableSwitchError("stored switch state is invalid")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if set(value) != {"schema_version", "enabled_harnesses"} or value["schema_version"] != "1.0":
            raise PortableSwitchError("stored switch state is invalid")
        if not isinstance(value["enabled_harnesses"], list):
            raise PortableSwitchError("stored switch state is invalid")
        transition(self.registry, value["enabled_harnesses"], (), turn_on=True)
        return value

    def _write_bytes(self, payload):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or (self.path.exists() and self.path.is_symlink()):
            raise PortableSwitchError("state root or switch path is invalid")
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".switch-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _write(self, value):
        payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
        self._write_bytes(payload)

    def _restore(self, prior_bytes):
        if prior_bytes is None:
            if self.path.exists():
                if self.path.is_symlink() or not self.path.is_file():
                    raise PortableSwitchError("switch path cannot be restored safely")
                self.path.unlink()
            return
        self._write_bytes(prior_bytes)

    def _verify_rows(self):
        rows = {}
        for name in self.registry:
            try:
                result = self.adapters[name].verify()
                state = result.get("state") if isinstance(result, dict) else None
                rows[name] = {"state": state} if state in {"ACTIVE", "INACTIVE"} else {
                    "state": "INVALID", "reason": "VERIFY_RESULT_INVALID"}
            except Exception:
                rows[name] = {"state": "INVALID", "reason": "VERIFY_ERROR"}
        return rows

    def _status_for(self, configured):
        configured = tuple(configured)
        rows = self._verify_rows()
        active = tuple(name for name in self.registry if rows[name].get("state") == "ACTIVE")
        consistent = all(
            rows[name].get("state") == ("ACTIVE" if name in configured else "INACTIVE")
            for name in self.registry
        )
        state = global_state(self.registry, active) if consistent else "INVALID"
        return {
            "global": state,
            "enabled_harnesses": list(active),
            "configured_harnesses": list(configured),
            "harnesses": rows,
        }

    def status(self):
        return self._status_for(self._read()["enabled_harnesses"])

    def _call(self, name, method, transaction_id, *args):
        result = getattr(self.adapters[name], method)(transaction_id, *args)
        expected = ADAPTER_RESULTS[method]
        if not isinstance(result, dict) or result.get("state") != expected:
            raise PortableSwitchError(f"adapter {method} result is invalid")
        return result

    def _compensate(self, transaction_id, targets, prior_bytes, before):
        errors = []
        for name in reversed(tuple(targets)):
            try:
                self._call(name, "rollback", transaction_id)
            except Exception:
                errors.append({"harness": name, "phase": "rollback"})
        try:
            self._restore(prior_bytes)
        except Exception:
            errors.append({"harness": "paper_state", "phase": "restore"})
        try:
            restored_bytes = self.path.read_bytes() if self.path.exists() else None
            if restored_bytes != prior_bytes:
                errors.append({"harness": "paper_state", "phase": "verify_restoration"})
        except Exception:
            errors.append({"harness": "paper_state", "phase": "verify_restoration"})
        restored = self._status_for(before)
        if restored["global"] == "INVALID":
            errors.append({"harness": "transaction", "phase": "verify_restoration"})
        return errors, restored

    def set(self, targets, *, turn_on):
        targets = tuple(targets)
        before_record = self._read()
        before = tuple(before_record["enabled_harnesses"])
        before_status = self._status_for(before)
        if before_status["global"] == "INVALID":
            return {"changed": False, "changed_harnesses": [], "result": "INVALID", "after": before_status}
        after = transition(self.registry, before, targets, turn_on=turn_on)
        changed_targets = tuple(
            name for name in targets if (name in before) != (name in after))
        if not changed_targets:
            return {"changed": False, "changed_harnesses": [], "result": before_status["global"], "after": before_status}

        prior_bytes = self.path.read_bytes() if self.path.exists() else None
        transaction_id = secrets.token_hex(16)
        desired = "ACTIVE" if turn_on else "INACTIVE"
        phase = "prepare"
        try:
            for name in changed_targets:
                self._call(name, "prepare", transaction_id, desired)
            phase = "commit"
            for name in changed_targets:
                self._call(name, "commit", transaction_id)
            phase = "verify_commit"
            committed = self._status_for(after)
            if committed["global"] == "INVALID":
                raise PortableSwitchError("committed adapter state failed verification")
            phase = "paper_state"
            self._write({"schema_version": "1.0", "enabled_harnesses": list(after)})
        except Exception:
            errors, restored = self._compensate(
                transaction_id, changed_targets, prior_bytes, before)
            result = "ROLLBACK_INCOMPLETE" if errors else "FAILED_ROLLED_BACK"
            return {
                "changed": False,
                "changed_harnesses": [],
                "result": result,
                "failed_phase": phase,
                "rollback_errors": errors,
                "after": restored,
            }

        finalize_errors = []
        for name in changed_targets:
            try:
                self._call(name, "finalize", transaction_id)
            except Exception:
                finalize_errors.append({"harness": name, "phase": "finalize"})
        final_status = self.status()
        if final_status["global"] == "INVALID":
            finalize_errors.append({"harness": "transaction", "phase": "verify_final"})
        return {
            "changed": True,
            "changed_harnesses": list(changed_targets),
            "result": "FINALIZE_INCOMPLETE" if finalize_errors else final_status["global"],
            "finalize_errors": finalize_errors,
            "after": final_status,
        }

    def refresh(self, targets):
        targets = tuple(targets)
        transition(self.registry, (), targets, turn_on=True)
        failures = []
        for name in targets:
            method = getattr(self.adapters[name], "refresh", None)
            if not callable(method):
                failures.append({"harness": name, "phase": "refresh", "reason": "ADAPTER_REQUIRED"})
                continue
            try:
                result = method()
                if not isinstance(result, dict) or result.get("state") != "REFRESHED":
                    failures.append({"harness": name, "phase": "refresh", "reason": "RESULT_INVALID"})
            except Exception:
                failures.append({"harness": name, "phase": "refresh", "reason": "REFRESH_ERROR"})
        status = self.status()
        return {
            "changed": False,
            "changed_harnesses": [],
            "result": "REFRESH_FAILED" if failures else status["global"],
            "refresh_errors": failures,
            "after": status,
        }
