"""Transactional global and per-harness adaptive production switches."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

from . import adaptive_production_authorization as auth
from .adaptive_parallel_activation import _atomic, load_policy, policy_contract_digest

HARNESS_REGISTRY = ("codex", "hermes")
RESULT_STATES = {"ACTIVE", "INACTIVE", "PENDING_REFRESH",
                 "FAILED_ROLLED_BACK", "INVALID"}


class SwitchError(ValueError):
    pass


def resolve_targets(*, global_selector, harnesses):
    if global_selector and harnesses:
        raise SwitchError("--global cannot be combined with --harness")
    if not global_selector and not harnesses:
        raise SwitchError("exactly one selector is required")
    targets = HARNESS_REGISTRY if global_selector else tuple(dict.fromkeys(harnesses))
    unknown = set(targets) - set(HARNESS_REGISTRY)
    if unknown:
        raise SwitchError("unsupported harness: " + ", ".join(sorted(unknown)))
    return tuple(target for target in HARNESS_REGISTRY if target in targets)


def derive_global_state(states):
    values = set(states.values())
    if values == {"ACTIVE"}:
        return "ACTIVE"
    if values == {"INACTIVE"}:
        return "INACTIVE"
    if values <= {"ACTIVE", "INACTIVE"}:
        return "PARTIAL"
    if "PENDING_REFRESH" in values:
        return "PENDING_REFRESH"
    return "INVALID"


class _StateLock:
    def __init__(self, path, timeout=10):
        self.path, self.timeout = Path(path), timeout

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise SwitchError("adaptive switch state is locked")
                time.sleep(.05)

    def __exit__(self, *_args):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


class AdaptiveSwitch:
    def __init__(self, *, policy_path, adapters=None, now=None,
                 qualification_path=None, receipts_path=None):
        self.policy_path = Path(policy_path)
        state = self.policy_path.parent
        if state.is_symlink():
            raise SwitchError("symlinked state roots are not allowed")
        self.authorization_path = state / "production-authorization.json"
        self.qualification_path = Path(qualification_path or state / "qualification.json")
        self.receipts_path = Path(receipts_path or state / "receipts")
        self.lock_path = state / ".switch.lock"
        self.now = now or (lambda: datetime.now(timezone.utc))
        if adapters is None:
            from .adaptive_runtime_refresh import InstalledRuntimeAdapter
            root = Path(__file__).resolve().parents[1]
            adapters = {name: InstalledRuntimeAdapter(name, root=root)
                        for name in HARNESS_REGISTRY}
        self.adapters = adapters

    def _envelope(self):
        if not self.authorization_path.is_file():
            return None
        return json.loads(self.authorization_path.read_text(encoding="utf-8"))

    def status(self, targets=None):
        selected = tuple(targets or HARNESS_REGISTRY)
        try:
            policy = load_policy(self.policy_path)
            envelope = self._envelope()
        except (OSError, ValueError, json.JSONDecodeError):
            policy, envelope = None, None
        enabled = set(auth.envelope_harnesses(envelope)) if envelope else set()
        rows = {}
        for harness in HARNESS_REGISTRY:
            if harness not in enabled:
                state = "INACTIVE" if policy and policy["activation_state"] == "inactive" else (
                    "INACTIVE" if envelope and envelope.get("schema_version") == "2.0" else "INVALID")
                rows[harness] = {"state": state, "reason": "NO_SIGNED_IDENTITY"}
                continue
            try:
                result = self.adapters[harness].verify(self.policy_path, now=self.now())
                rows[harness] = dict(result)
            except Exception as exc:
                rows[harness] = {"state": "INVALID",
                                 "reason": type(exc).__name__.upper()}
        states = {name: rows[name]["state"] for name in HARNESS_REGISTRY}
        active = [name for name in HARNESS_REGISTRY if states[name] == "ACTIVE"]
        return {"schema_version": "1.0", "global": derive_global_state(states),
                "enabled_harnesses": active, "harnesses": rows,
                "authorization_basis": auth.STATE if active else None,
                "routing": {"prime": "unchanged", "omp": "unchanged",
                            "t0_t4": "unchanged", "s0_s4": "unchanged"},
                "selected": list(selected)}

    def _candidate_policy(self, envelope):
        policy = copy.deepcopy(load_policy(self.policy_path))
        values = envelope["authorizations"]
        active = bool(values)
        policy["activation_state"] = "production" if active else "inactive"
        reference = auth.digest(envelope) if active else None
        expiry = (min(auth._utc(item["payload"]["expires_at"]) for item in values.values())
                  .isoformat().replace("+00:00", "Z")) if active else None
        for name, ceiling in (("execution", 8), ("reviewer", 4)):
            policy[name].update(enabled=active,
                                qualification_state=auth.STATE if active else "disabled",
                                initial_ceiling=2 if active else 0,
                                qualified_ceiling=ceiling if active else 0)
        for grant in policy["grants"].values():
            grant.update(enabled=active, state=auth.STATE if active else "disabled",
                         qualification_reference=reference, expiry=expiry)
        return policy

    @staticmethod
    def _prior(path):
        return path.read_bytes() if path.exists() else None

    @staticmethod
    def _restore(path, value):
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".restore")
            temporary.write_bytes(value)
            os.replace(temporary, path)

    def _receipt(self, value):
        self.receipts_path.mkdir(parents=True, exist_ok=True)
        receipt = {**value, "created_at": self.now().isoformat().replace("+00:00", "Z")}
        receipt["receipt_sha256"] = auth.digest(receipt)
        _atomic(self.receipts_path / f'{receipt["receipt_sha256"]}.json', receipt)
        return receipt["receipt_sha256"]

    def _commit(self, *, command, targets, envelope, raise_on_failure):
        prior_policy = self._prior(self.policy_path)
        prior_envelope = self._prior(self.authorization_path)
        before = self.status()
        try:
            _atomic(self.authorization_path, envelope)
            _atomic(self.policy_path, self._candidate_policy(envelope))
            refreshes = {}
            if command == "on":
                refreshes = {name: self.adapters[name].refresh(self.policy_path)
                             for name in targets}
                blocked = {name: row for name, row in refreshes.items()
                           if row.get("state") != "ACTIVE"}
                if blocked:
                    raise SwitchError("consumer refresh failed: " + ",".join(blocked))
            checks = {name: self.adapters[name].verify(
                self.policy_path, now=self.now()) for name in envelope["authorizations"]}
            failed = {name: row for name, row in checks.items()
                      if row.get("state") != "ACTIVE"}
            if failed:
                raise SwitchError("consumer verification failed: " + ",".join(failed))
        except Exception as exc:
            self._restore(self.policy_path, prior_policy)
            self._restore(self.authorization_path, prior_envelope)
            receipt = self._receipt({"action": command, "targets": list(targets),
                                     "result": "FAILED_ROLLED_BACK",
                                     "reason": type(exc).__name__})
            result = {"command": command, "result": "FAILED_ROLLED_BACK",
                      "changed": False, "before": before, "after": self.status(),
                      "receipt": receipt}
            if raise_on_failure:
                raise SwitchError(str(exc)) from exc
            return result
        after = self.status()
        receipt = self._receipt({"action": command, "targets": list(targets),
                                 "result": "ACTIVE" if envelope["authorizations"]
                                 else "INACTIVE"})
        return {"command": command, "result": after["global"], "changed": True,
                "before": before, "after": after, "receipt": receipt,
                "refresh": refreshes if command == "on" else {}}

    def _entry(self, harness, expires_at):
        expiry = (expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                  if isinstance(expires_at, datetime) else expires_at)
        try:
            payload = auth.build_harness_payload(
                policy_path=self.policy_path, harness=harness,
                identities=self.adapters[harness].observe(),
                authorization="Explicit operator adaptive-parallel on command.",
                expires_at=expiry, now=self.now())
            signed = self.adapters[harness].sign(payload)
        except Exception as exc:
            raise SwitchError(str(exc)) from exc
        return {"payload": payload, "signatures": [signed]}

    def enable(self, targets, *, expires_at=None, raise_on_failure=True):
        targets = tuple(targets)
        if set(targets) - set(HARNESS_REGISTRY) or not targets:
            raise SwitchError("unsupported or empty harness target")
        current = self.status()
        if all(current["harnesses"][name]["state"] == "ACTIVE" for name in targets):
            return {"command": "on", "result": current["global"], "changed": False,
                    "before": current, "after": current, "receipt": None}
        expiry = expires_at or (self.now() + timedelta(days=30))
        with _StateLock(self.lock_path):
            existing = self._envelope()
            authorizations = (copy.deepcopy(existing["authorizations"])
                              if existing and existing.get("schema_version") == "2.0"
                              else {})
            for name in targets:
                authorizations[name] = self._entry(name, expiry)
            envelope = {"schema_version": "2.0", "kind": auth.STATE,
                        "authorizations": authorizations}
            binding_sha = policy_contract_digest(self._candidate_policy(envelope))
            for name in targets:
                payload = envelope["authorizations"][name]["payload"]
                payload["source_binding"]["policy_sha256"] = binding_sha
                envelope["authorizations"][name]["signatures"] = [
                    self.adapters[name].sign(payload)]
            return self._commit(command="on", targets=targets, envelope=envelope,
                                raise_on_failure=raise_on_failure)

    def disable(self, targets, *, raise_on_failure=True):
        targets = tuple(targets)
        if set(targets) - set(HARNESS_REGISTRY) or not targets:
            raise SwitchError("unsupported or empty harness target")
        current = self.status()
        if all(current["harnesses"][name]["state"] == "INACTIVE" for name in targets):
            return {"command": "off", "result": current["global"], "changed": False,
                    "before": current, "after": current, "receipt": None}
        with _StateLock(self.lock_path):
            existing = self._envelope()
            authorizations = (copy.deepcopy(existing["authorizations"])
                              if existing and existing.get("schema_version") == "2.0"
                              else {})
            if existing and existing.get("schema_version") != "2.0":
                remaining = set(auth.envelope_harnesses(existing)) - set(targets)
                expiry = self.now() + timedelta(days=30)
                authorizations = {name: self._entry(name, expiry)
                                  for name in remaining}
            for name in targets:
                authorizations.pop(name, None)
            envelope = {"schema_version": "2.0", "kind": auth.STATE,
                        "authorizations": authorizations}
            return self._commit(command="off", targets=targets, envelope=envelope,
                                raise_on_failure=raise_on_failure)

    def refresh(self, targets):
        results = {name: self.adapters[name].refresh(self.policy_path)
                   for name in targets}
        states = {name: value["state"] for name, value in results.items()}
        result = ("PENDING_REFRESH" if "PENDING_REFRESH" in states.values()
                  else "INVALID" if "INVALID" in states.values() else "ACTIVE")
        return {"command": "refresh", "result": result, "changed": False,
                "harnesses": results, "after": self.status()}
