"""Transactional registration, movement, and removal of parallel providers."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any

from .job_ledger import JobLedger
from .orchestration_switch import load_matrix
from .parallel_provider import ParallelProviderCatalog


class ProviderManagerError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProviderManagerError("provider management document is invalid")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".provider-", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ParallelProviderManager:
    def __init__(self, *, root: Path, manifest: Path, catalog: Path,
                 capability_policy: Path, switch_path: Path,
                 backup_root: Path):
        self.root = root.resolve()
        self.manifest_path = manifest.resolve()
        self.catalog_path = catalog.resolve()
        self.capability_policy_path = capability_policy.resolve()
        self.switch_path = switch_path.resolve()
        self.backup_root = backup_root.resolve()
        self.manifest = _read_json(self.manifest_path)
        if (set(self.manifest) != {
                "schema_version", "name", "catalog_entry", "capability_aliases",
                "owned_files", "preserved_evidence"}
                or self.manifest["schema_version"] != "1.0"
                or not isinstance(self.manifest["owned_files"], list)):
            raise ProviderManagerError("parallel provider manifest is invalid")
        for relative in self.manifest["owned_files"]:
            candidate = (self.root / PurePosixPath(relative)).resolve()
            if not candidate.is_relative_to(self.root) or candidate == self.root:
                raise ProviderManagerError("parallel provider file escapes root")

    @property
    def name(self) -> str:
        return self.manifest["name"]

    def status(self) -> dict[str, Any]:
        catalog = _read_json(self.catalog_path)
        policy = _read_json(self.capability_policy_path)
        entry = catalog.get("providers", {}).get(self.name)
        aliases = self.manifest["capability_aliases"]
        return {
            "provider": self.name,
            "registered": entry is not None,
            "consumers": list(entry.get("consumers", [])) if isinstance(entry, dict) else [],
            "capabilities_registered": all(
                policy.get("aliases", {}).get(alias) == value
                for alias, value in aliases.items()
            ),
            "owned_files_present": sum(
                (self.root / PurePosixPath(relative)).is_file()
                for relative in self.manifest["owned_files"]
            ),
            "owned_files_total": len(self.manifest["owned_files"]),
            "evidence_policy": "preserve",
        }

    def _snapshot(self) -> str:
        material = (
            self.catalog_path.read_bytes() + self.capability_policy_path.read_bytes()
            + datetime.now(timezone.utc).isoformat().encode()
        )
        identity = hashlib.sha256(material).hexdigest()[:16]
        destination = self.backup_root / identity
        if destination.exists():
            raise ProviderManagerError("provider snapshot already exists")
        destination.mkdir(parents=True)
        shutil.copyfile(self.catalog_path, destination / "parallel-worker-providers.json")
        shutil.copyfile(self.capability_policy_path, destination / "prime-capability-policy.json")
        files = destination / "files"
        for relative in self.manifest["owned_files"]:
            source = self.root / PurePosixPath(relative)
            if source.is_file():
                target = files / PurePosixPath(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        _atomic_json(destination / "snapshot.json", {
            "schema_version": "1.0", "provider": self.name,
            "snapshot": identity, "evidence_preserved": True,
        })
        return identity

    def bind(self, consumers, *, apply: bool) -> dict[str, Any]:
        selected = tuple(dict.fromkeys(consumers))
        if not selected or set(selected) - {"router", "evaluator"}:
            raise ProviderManagerError("provider consumer is invalid")
        if not apply:
            return {"status": "DRY-RUN", "operation": "bind", "consumers": list(selected)}
        catalog = _read_json(self.catalog_path)
        if self.name not in catalog.get("providers", {}):
            raise ProviderManagerError("parallel provider is not registered")
        snapshot = self._snapshot()
        catalog["providers"][self.name]["consumers"] = list(selected)
        _atomic_json(self.catalog_path, catalog)
        ParallelProviderCatalog.load(self.catalog_path)
        return {"status": "PASS", "operation": "bind", "consumers": list(selected), "snapshot": snapshot}

    def register(self, *, apply: bool) -> dict[str, Any]:
        if not apply:
            return {"status": "DRY-RUN", "operation": "register"}
        catalog = _read_json(self.catalog_path)
        policy = _read_json(self.capability_policy_path)
        snapshot = self._snapshot()
        catalog.setdefault("providers", {})[self.name] = self.manifest["catalog_entry"]
        policy.setdefault("aliases", {}).update(self.manifest["capability_aliases"])
        _atomic_json(self.catalog_path, catalog)
        _atomic_json(self.capability_policy_path, policy)
        ParallelProviderCatalog.load(self.catalog_path)
        return {"status": "PASS", "operation": "register", "snapshot": snapshot}

    def _removal_ready(self, *, ledger: Path, allowed_roots) -> None:
        matrix = load_matrix(self.switch_path)
        if any(values["orca"] != "off" for values in matrix.values()):
            raise ProviderManagerError("Orca must be verified off before removal")
        if not ledger.is_file():
            raise ProviderManagerError("Prime ledger is required before removal")
        job_ledger = JobLedger(ledger, allowed_roots)
        service = ParallelProviderCatalog.load(self.catalog_path).build_service(
            self.name, ledger=job_ledger, allowed_roots=allowed_roots,
            root=self.root, switch_path=self.switch_path,
        )
        if service.provider.active_workers():
            raise ProviderManagerError("active Orca workers block removal")

    def unregister(self, *, ledger: Path, allowed_roots, apply: bool,
                   uninstall: bool = False) -> dict[str, Any]:
        operation = "uninstall" if uninstall else "unregister"
        if not apply:
            return {"status": "DRY-RUN", "operation": operation, "evidence": "preserved"}
        self._removal_ready(ledger=ledger, allowed_roots=allowed_roots)
        catalog = _read_json(self.catalog_path)
        policy = _read_json(self.capability_policy_path)
        snapshot = self._snapshot()
        catalog.get("providers", {}).pop(self.name, None)
        for alias in self.manifest["capability_aliases"]:
            policy.get("aliases", {}).pop(alias, None)
        _atomic_json(self.catalog_path, catalog)
        _atomic_json(self.capability_policy_path, policy)
        removed = []
        if uninstall:
            for relative in self.manifest["owned_files"]:
                target = self.root / PurePosixPath(relative)
                if target.is_file():
                    target.unlink()
                    removed.append(relative)
        return {
            "status": "PASS", "operation": operation, "snapshot": snapshot,
            "removed_files": removed, "evidence": "preserved",
        }

    def restore(self, snapshot: str, *, apply: bool) -> dict[str, Any]:
        if len(snapshot) != 16 or any(char not in "0123456789abcdef" for char in snapshot):
            raise ProviderManagerError("provider snapshot is invalid")
        source = self.backup_root / snapshot
        descriptor = _read_json(source / "snapshot.json")
        if descriptor.get("snapshot") != snapshot or descriptor.get("provider") != self.name:
            raise ProviderManagerError("provider snapshot is invalid")
        if not apply:
            return {"status": "DRY-RUN", "operation": "restore", "snapshot": snapshot}
        shutil.copyfile(source / "parallel-worker-providers.json", self.catalog_path)
        shutil.copyfile(source / "prime-capability-policy.json", self.capability_policy_path)
        files = source / "files"
        for relative in self.manifest["owned_files"]:
            prior = files / PurePosixPath(relative)
            if prior.is_file():
                target = self.root / PurePosixPath(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(prior, target)
        return {"status": "PASS", "operation": "restore", "snapshot": snapshot}


__all__ = ["ParallelProviderManager", "ProviderManagerError"]
