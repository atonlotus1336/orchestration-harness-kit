#!/usr/bin/env python3
"""Build the Phase 01 Prime probe from consumer-owned, retained file handles."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from typing import Any


class IntegrityError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def identity_from_stat(result: os.stat_result, digest: str, relative: str) -> dict[str, Any]:
    return {
        "path": relative,
        "uid": result.st_uid,
        "gid": result.st_gid,
        "mode": stat.S_IMODE(result.st_mode),
        "device": result.st_dev,
        "inode": result.st_ino,
        "size": result.st_size,
        "mtime_ns": result.st_mtime_ns,
        "sha256": digest,
    }


def identity_for_path(path: Path, relative: str) -> dict[str, Any]:
    result = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(result.st_mode):
        raise IntegrityError(f"not a regular file: {relative}")
    return identity_from_stat(result, sha256_path(path), relative)


class FdEntry:
    def __init__(self, path: Path, record: dict[str, Any], fd: int):
        self.path = path
        self.record = record
        self.fd = fd

    @classmethod
    def open_verified(cls, path: Path, record: dict[str, Any]) -> "FdEntry":
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        entry = cls(path, dict(record), fd)
        try:
            entry.revalidate_handle()
            entry.revalidate_path_and_handle()
            return entry
        except BaseException:
            entry.close()
            raise

    def read_all(self) -> bytes:
        result = os.fstat(self.fd)
        remaining = result.st_size
        offset = 0
        chunks: list[bytes] = []
        while remaining:
            chunk = os.pread(self.fd, min(1024 * 1024, remaining), offset)
            if not chunk:
                raise IntegrityError(f"short read: {self.record['path']}")
            chunks.append(chunk)
            offset += len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def current_handle_identity(self) -> dict[str, Any]:
        result = os.fstat(self.fd)
        return identity_from_stat(result, sha256_bytes(self.read_all()), self.record["path"])

    @staticmethod
    def _persisted_identity(identity: dict[str, Any]) -> dict[str, Any]:
        """Return fields stable across a WSL VM/DrvFS device re-enumeration.

        DrvFS assigns a boot-local st_dev value.  The persisted source record
        retains it as evidence, but it cannot authorize or reject a later WSL
        boot.  Live path-versus-handle checks below still compare the complete
        identity (including device and inode) to detect replacement races.
        """

        return {key: value for key, value in identity.items() if key != "device"}

    def revalidate_handle(self) -> None:
        if self._persisted_identity(self.current_handle_identity()) != self._persisted_identity(self.record):
            raise IntegrityError(f"open-handle identity drift: {self.record['path']}")

    def revalidate_path_and_handle(self) -> None:
        self.revalidate_handle()
        path_identity = identity_for_path(self.path, self.record["path"])
        handle_identity = self.current_handle_identity()
        if path_identity != handle_identity:
            raise IntegrityError(f"path replacement detected: {self.record['path']}")
        if self._persisted_identity(path_identity) != self._persisted_identity(self.record):
            raise IntegrityError(f"path identity drift: {self.record['path']}")

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class SourceFdConsumer:
    BUILD_INPUTS = (
        "Containerfile",
        "requirements.in",
        "requirements.lock",
        "relay-config.schema.json",
        "relay_client.py",
    )

    def __init__(self, repo_root: Path, source_root: Path, entries: list[FdEntry], epoch: int):
        self.repo_root = repo_root
        self.source_root = source_root
        self.entries = entries
        self.extra_entries: list[FdEntry] = []
        self.epoch = epoch

    @classmethod
    def from_repository(cls, repo_root: Path) -> "SourceFdConsumer":
        lock = json.loads((repo_root / "containers/prime-probe/prime-source.lock.json").read_text(encoding="utf-8"))
        provenance_path = repo_root / lock["provenance_path"]
        if sha256_path(provenance_path) != lock["provenance_sha256"]:
            raise IntegrityError("Prime provenance bytes changed")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        record = provenance["record"]
        for key in ("commit", "tree", "package_lock_sha256"):
            if record[key] != lock[key]:
                raise IntegrityError(f"Prime source lock mismatch: {key}")
        source_root = repo_root / lock["source_root"]
        entries: list[FdEntry] = []
        try:
            for member in sorted(record["members"], key=lambda item: item["path"]):
                entries.append(FdEntry.open_verified(source_root / member["path"], member))
        except BaseException:
            for entry in entries:
                entry.close()
            raise
        return cls(repo_root, source_root, entries, lock["source_date_epoch"])

    def __enter__(self) -> "SourceFdConsumer":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        for entry in self.entries + self.extra_entries:
            entry.close()

    def add_build_inputs(self, probe_dir: Path) -> None:
        if self.extra_entries:
            return
        for name in self.BUILD_INPUTS:
            path = probe_dir / name
            record = identity_for_path(path, name)
            self.extra_entries.append(FdEntry.open_verified(path, record))

    def revalidate(self) -> None:
        for entry in self.entries + self.extra_entries:
            entry.revalidate_path_and_handle()

    def snapshot(self) -> list[dict[str, Any]]:
        return [entry.current_handle_identity() for entry in self.entries + self.extra_entries]

    def deterministic_context_bytes(self, probe_dir: Path) -> bytes:
        self.add_build_inputs(probe_dir)
        self.revalidate()
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            members: list[tuple[str, FdEntry]] = [(f"prime/{entry.record['path']}", entry) for entry in self.entries]
            members.extend((entry.record["path"], entry) for entry in self.extra_entries)
            for archive_name, entry in sorted(members, key=lambda item: item[0]):
                data = entry.read_all()
                info = tarfile.TarInfo(archive_name)
                info.size = len(data)
                info.mtime = self.epoch
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mode = 0o755 if archive_name.endswith((".sh", "relay_client.py")) else 0o644
                archive.addfile(info, io.BytesIO(data))
        self.revalidate()
        return output.getvalue()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(command: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(command, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        stderr = result.stderr.decode("utf-8", "replace")[-4000:]
        raise IntegrityError(f"command failed ({command[0]}): {stderr}")
    return result


def podman_json(arguments: list[str]) -> Any:
    return json.loads(run(["podman", *arguments]).stdout)


def image_file_hashes(tag: str) -> dict[str, Any]:
    code = r'''
import hashlib, json, os, stat
result = {}
excluded = {"/dev", "/proc", "/sys", "/run", "/tmp", "/work"}
for base in ("/opt", "/usr/local", "/root"):
    for root, dirs, files in os.walk(base, topdown=True, followlinks=False):
        dirs[:] = [name for name in dirs if os.path.join(root, name) not in excluded]
        for name in files:
            path = os.path.join(root, name)
            try:
                mode = os.lstat(path).st_mode
                if stat.S_ISLNK(mode):
                    st = os.lstat(path)
                    result[path] = {"type": "link", "target": os.readlink(path), "mode": stat.S_IMODE(st.st_mode), "uid": st.st_uid, "gid": st.st_gid, "mtime_ns": st.st_mtime_ns}
                elif stat.S_ISREG(mode):
                    st = os.lstat(path)
                    digest = hashlib.sha256()
                    with open(path, "rb") as handle:
                        for chunk in iter(lambda: handle.read(1048576), b""):
                            digest.update(chunk)
                    result[path] = {"type": "file", "sha256": digest.hexdigest(), "mode": stat.S_IMODE(st.st_mode), "uid": st.st_uid, "gid": st.st_gid, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
            except FileNotFoundError:
                pass
print(json.dumps(result, sort_keys=True))
'''
    return json.loads(run(["podman", "run", "--rm", "--user", "0", "--entrypoint", "python", tag, "-c", code]).stdout)


def diagnose_existing_images(first: str, second: str) -> dict[str, Any]:
    a = image_file_hashes(first)
    b = image_file_hashes(second)
    paths = sorted(set(a) | set(b))
    differences = [{"path": path, "first": a.get(path), "second": b.get(path)} for path in paths if a.get(path) != b.get(path)]
    return {"first_files": len(a), "second_files": len(b), "difference_count": len(differences), "differences": differences[:200]}


def canonical_image_evidence(tag: str, inspected: dict[str, Any]) -> dict[str, Any]:
    files = image_file_hashes(tag)
    application_manifest = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    runtime = {
        "architecture": inspected["Architecture"],
        "os": inspected["Os"],
        "manifest_type": inspected["ManifestType"],
        "user": inspected["Config"].get("User"),
        "env": inspected["Config"].get("Env", []),
        "entrypoint": inspected["Config"].get("Entrypoint", []),
        "cmd": inspected["Config"].get("Cmd", []),
        "working_dir": inspected["Config"].get("WorkingDir"),
        "labels": inspected.get("Labels", {}),
        "base_layers": inspected["RootFS"]["Layers"][:-1],
    }
    runtime_manifest = json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode()
    return {
        "application_file_count": len(files),
        "application_manifest_sha256": sha256_bytes(application_manifest),
        "runtime_config_sha256": sha256_bytes(runtime_manifest),
        "base_layer_digests": runtime["base_layers"],
        "platform": f"{runtime['os']}/{runtime['architecture']}",
    }


def write_not_run(prerequisite: Path, output: Path, reason: str) -> dict[str, Any]:
    value = {
        "schema_version": "1.0",
        "status": "NOT_RUN",
        "go_eligible": False,
        "reason": reason[:512],
        "prerequisite_sha256": sha256_path(prerequisite),
        "build_started": False,
        "runnable_image": None,
        "sbom_sha256": None,
        "builds": [],
    }
    atomic_json(output, value)
    return value


def flatten_npm(value: dict[str, Any]) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    def visit(node: dict[str, Any]) -> None:
        for name, child in node.get("dependencies", {}).items():
            found.add((name, str(child.get("version", "UNKNOWN"))))
            visit(child)
    visit(value)
    return sorted(found)


def generate_sbom(tag: str, probe_dir: Path, context_sha: str, image_id: str) -> dict[str, Any]:
    npm_inventory = json.loads(run([
        "podman", "run", "--rm", "--workdir", "/opt/prime-agent",
        "--entrypoint", "npm", tag, "ls", "--all", "--json",
    ]).stdout)
    python_code = "import importlib.metadata as m,json; print(json.dumps(sorted((d.metadata['Name'],d.version) for d in m.distributions())))"
    python_inventory = json.loads(run(["podman", "run", "--rm", "--entrypoint", "python", tag, "-c", python_code]).stdout)
    packages: list[dict[str, Any]] = []
    for ecosystem, items in (("npm", flatten_npm(npm_inventory)), ("pypi", python_inventory)):
        for index, (name, version) in enumerate(items):
            token = hashlib.sha256(f"{ecosystem}:{name}:{version}".encode()).hexdigest()[:20]
            packages.append({
                "SPDXID": f"SPDXRef-{ecosystem}-{token}",
                "name": name,
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "supplier": "NOASSERTION",
            })
    namespace = hashlib.sha256(f"{image_id}:{context_sha}".encode()).hexdigest()
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "hermes-prime-agent-probe-v0.7.1",
        "documentNamespace": f"https://__PRIVATE_TOKEN_02__.local/spdx/prime-probe/{namespace}",
        "creationInfo": {"created": "2026-08-07T17:17:26Z", "creators": ["Tool: hermes-phase01-build-image"]},
        "packages": sorted(packages, key=lambda item: item["SPDXID"]),
        "annotations": [{"annotationType": "OTHER", "annotator": "Tool: hermes-phase01-build-image", "annotationDate": "2026-08-07T17:17:26Z", "comment": f"context-sha256={context_sha};image-id={image_id}"}],
    }


def build_ready(repo_root: Path, prerequisite: Path, output: Path) -> dict[str, Any]:
    probe_dir = repo_root / "containers/prime-probe"
    lock = json.loads((probe_dir / "prime-source.lock.json").read_text(encoding="utf-8"))
    if sha256_path(prerequisite) != lock["podman_prerequisite_sha256"]:
        raise IntegrityError("Podman prerequisite bytes changed")
    builds: list[dict[str, Any]] = []
    with SourceFdConsumer.from_repository(repo_root) as consumer:
        context = consumer.deterministic_context_bytes(probe_dir)
        context_sha = sha256_bytes(context)
        context_parent = repo_root / ".git/phase01-gap-lifecycle/build-contexts"
        context_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="prime-probe.", dir=context_parent) as temporary:
            context_dir = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(context), mode="r:") as archive:
                for member in archive.getmembers():
                    destination = (context_dir / member.name).resolve()
                    if context_dir.resolve() not in destination.parents:
                        raise IntegrityError("unsafe build context member")
                archive.extractall(context_dir, filter="data")
            for suffix in ("a", "b"):
                tag = f"localhost/hermes-prime-probe:phase01-{suffix}"
                command = [
                    "podman", "build", "--format", "oci", "--layers=false", "--no-cache",
                    "--timestamp", str(lock["source_date_epoch"]), "--pull=never",
                    "--build-arg", f"NODE_BASE={lock['node_base']}",
                    "--build-arg", f"PYTHON_BASE={lock['python_base']}",
                    "--build-arg", f"PRIME_COMMIT={lock['commit']}",
                    "--build-arg", f"PRIME_TREE={lock['tree']}",
                    "--build-arg", f"SOURCE_CONTEXT_SHA256={context_sha}",
                    "--tag", tag, "-f", str(context_dir / "Containerfile"), str(context_dir),
                ]
                run(command)
                consumer.revalidate()
                inspected = podman_json(["image", "inspect", tag])[0]
                digest = inspected.get("Digest") or inspected["Id"]
                canonical = canonical_image_evidence(tag, inspected)
                builds.append({
                    "tag": tag,
                    "image_id": inspected["Id"],
                    "manifest_sha256": digest,
                    "labels": inspected.get("Labels", {}),
                    **canonical,
                })
        consumer.revalidate()
    comparable = ("application_file_count", "application_manifest_sha256", "runtime_config_sha256", "base_layer_digests", "platform", "labels")
    if any(builds[0][field] != builds[1][field] for field in comparable):
        diagnosis = diagnose_existing_images(builds[0]["tag"], builds[1]["tag"])
        raise IntegrityError(f"two fixed-epoch application builds differ: {json.dumps(diagnosis, sort_keys=True)}")
    final_tag = "localhost/hermes-prime-probe:phase01"
    run(["podman", "tag", builds[0]["tag"], final_tag])
    digest = builds[0]["manifest_sha256"]
    if not str(digest).startswith("sha256:"):
        raise IntegrityError("built image lacks sha256 identity")
    sbom_path = probe_dir / "sbom.spdx.json"
    atomic_json(sbom_path, generate_sbom(final_tag, probe_dir, context_sha, builds[0]["image_id"]))
    sbom_sha = sha256_path(sbom_path)
    value = {
        "schema_version": "1.0",
        "status": "READY",
        "go_eligible": True,
        "reason": "",
        "prerequisite_sha256": sha256_path(prerequisite),
        "provenance_sha256": lock["provenance_sha256"],
        "source_context_sha256": context_sha,
        "source_commit": lock["commit"],
        "source_tree": lock["tree"],
        "node_base": lock["node_base"],
        "python_base": lock["python_base"],
        "build_started": True,
        "builds": builds,
        "canonical_reproducibility": True,
        "raw_storage_digest_equal": builds[0]["manifest_sha256"] == builds[1]["manifest_sha256"],
        "runnable_image": f"localhost/hermes-prime-probe@{digest}",
        "local_image_id": builds[0]["image_id"],
        "sbom_path": "containers/prime-probe/sbom.spdx.json",
        "sbom_sha256": sbom_sha,
        "runtime_contract": {"pull": False, "rebuild": False, "mutable_tag": False},
    }
    atomic_json(output, value)
    return value


def dispatch(repo_root: Path, prerequisite: Path, output: Path, *, build: bool = True) -> dict[str, Any]:
    prerequisite_value = json.loads(prerequisite.read_text(encoding="utf-8"))
    if prerequisite_value.get("status") != "READY" or prerequisite_value.get("go_eligible") is not True:
        return write_not_run(prerequisite, output, str(prerequisite_value.get("reason") or "Podman prerequisite unavailable"))
    if not build:
        raise IntegrityError("READY prerequisite requires the real build path")
    return build_ready(repo_root, prerequisite, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--prerequisite", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--diagnose-existing", action="store_true")
    args = parser.parse_args()
    if args.diagnose_existing:
        print(json.dumps(diagnose_existing_images("localhost/hermes-prime-probe:phase01-a", "localhost/hermes-prime-probe:phase01-b"), indent=2, sort_keys=True))
        return 0
    repo = args.repo.resolve()
    prerequisite = args.prerequisite or repo / ".planning/phases/01-contracts-feasibility/evidence/phase01-podman-prerequisite.json"
    output = args.output or repo / "containers/prime-probe/image-lock.json"
    result = dispatch(repo, prerequisite.resolve(), output.resolve(), build=args.build)
    print(json.dumps({"status": result["status"], "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
