"""Fail-closed rootless Podman boundary for the Phase 01 Prime probe."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import time
from typing import Any, Sequence
import xml.etree.ElementTree as ET


class ContainmentError(RuntimeError):
    """A prerequisite, launch, or teardown invariant failed."""


def validate_junit_counts(path: Path) -> dict[str, int]:
    """Reject an empty, skipped, failed, or malformed containment suite."""

    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise ContainmentError("JUnit document contains no test suites")
    counts = {
        field: sum(int(suite.attrib.get(field, "0")) for suite in suites)
        for field in ("tests", "failures", "errors", "skipped")
    }
    if counts["tests"] <= 0 or any(counts[field] != 0 for field in ("failures", "errors", "skipped")):
        raise ContainmentError(f"containment suite is not clean: {counts}")
    return counts


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(
    arguments: Sequence[str], *, check: bool = True, pass_fds: tuple[int, ...] = ()
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(arguments), check=check, text=True, capture_output=True, pass_fds=pass_fds)


@dataclasses.dataclass
class VerifiedArtifact:
    path: Path
    fd: int
    identity: tuple[int, int, int, int, int, int, int]
    sha256: str
    data: bytes

    @classmethod
    def open(cls, path: Path, expected_sha256: str | None = None) -> "VerifiedArtifact":
        resolved = path.resolve(strict=True)
        fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            stat = os.fstat(fd)
            data = b""
            while chunk := os.read(fd, 1024 * 1024):
                data += chunk
            digest = _sha256(data)
            if expected_sha256 is not None and digest != expected_sha256:
                raise ContainmentError(f"artifact hash mismatch: {resolved}")
            identity = (stat.st_uid, stat.st_gid, stat.st_mode, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            return cls(resolved, fd, identity, digest, data)
        except BaseException:
            os.close(fd)
            raise

    def revalidate(self) -> None:
        path_stat = os.stat(self.path, follow_symlinks=False)
        fd_stat = os.fstat(self.fd)
        current = (fd_stat.st_uid, fd_stat.st_gid, fd_stat.st_mode, fd_stat.st_dev, fd_stat.st_ino, fd_stat.st_size, fd_stat.st_mtime_ns)
        path_identity = (path_stat.st_uid, path_stat.st_gid, path_stat.st_mode, path_stat.st_dev, path_stat.st_ino, path_stat.st_size, path_stat.st_mtime_ns)
        if current != self.identity or path_identity != self.identity:
            raise ContainmentError(f"artifact replaced after admission: {self.path}")
        os.lseek(self.fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while chunk := os.read(self.fd, 1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != self.sha256:
            raise ContainmentError(f"artifact bytes changed after admission: {self.path}")

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@dataclasses.dataclass
class Admission:
    repo: Path
    prerequisite: VerifiedArtifact
    image_lock: VerifiedArtifact
    sbom: VerifiedArtifact
    image: str
    image_id: str
    image_value: dict[str, Any]

    def revalidate(self) -> None:
        for artifact in (self.prerequisite, self.image_lock, self.sbom):
            artifact.revalidate()
        inspected = _podman_json("image", "inspect", self.image)[0]
        digest = inspected.get("Digest")
        if digest is None or not self.image.endswith(f"@{digest}") or inspected["Id"] != self.image_id:
            raise ContainmentError("immutable image identity changed after admission")

    def close(self) -> None:
        for artifact in (self.prerequisite, self.image_lock, self.sbom):
            artifact.close()

    def __enter__(self) -> "Admission":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _podman_json(*arguments: str) -> Any:
    return json.loads(_run(["podman", *arguments]).stdout)


def admit(repo: Path) -> Admission:
    repo = repo.resolve()
    if os.name != "posix" or os.environ.get("WSL_DISTRO_NAME") != "Ubuntu-restored":
        raise ContainmentError("containment is admitted only in Ubuntu-restored WSL")
    if os.geteuid() == 0:
        raise ContainmentError("Podman containment must run as the normal rootless user")

    lock_path = repo / "containers/prime-probe/prime-source.lock.json"
    source_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    prerequisite = VerifiedArtifact.open(repo / source_lock["podman_prerequisite_path"], source_lock["podman_prerequisite_sha256"])
    image_lock: VerifiedArtifact | None = None
    sbom: VerifiedArtifact | None = None
    try:
        prerequisite_value = json.loads(prerequisite.data)
        if prerequisite_value.get("status") != "READY" or prerequisite_value.get("go_eligible") is not True:
            raise ContainmentError("Podman prerequisite is not READY")
        image_lock = VerifiedArtifact.open(repo / "containers/prime-probe/image-lock.json")
        image_value = json.loads(image_lock.data)
        if image_value.get("status") != "READY" or image_value.get("go_eligible") is not True:
            raise ContainmentError("image lock is not READY")
        if image_value.get("prerequisite_sha256") != prerequisite.sha256:
            raise ContainmentError("image lock is not bound to the prerequisite")
        contract = image_value.get("runtime_contract")
        if contract != {"pull": False, "rebuild": False, "mutable_tag": False}:
            raise ContainmentError("image runtime contract is not immutable")
        image = str(image_value.get("runnable_image"))
        if image.count("@sha256:") != 1 or ":phase01" in image:
            raise ContainmentError("runnable image is not an immutable digest")
        sbom = VerifiedArtifact.open(repo / image_value["sbom_path"], image_value["sbom_sha256"])

        info = _podman_json("info", "--format", "json")
        host = info["host"]
        if host["security"]["rootless"] is not True or host["cgroupVersion"] != "v2" or host["ociRuntime"]["name"] != "crun":
            raise ContainmentError("rootless Podman/cgroup-v2/crun admission failed")
        inspected = _podman_json("image", "inspect", image)[0]
        expected = image_value["builds"][0]
        labels = inspected.get("Labels") or inspected["Config"].get("Labels", {})
        required_labels = {
            "org.opencontainers.image.revision": image_value["source_commit"],
            "ai.__PRIVATE_TOKEN_02__.prime.tree": image_value["source_tree"],
            "ai.__PRIVATE_TOKEN_02__.prime.context.sha256": image_value["source_context_sha256"],
            "ai.__PRIVATE_TOKEN_02__.prime.mode": "rpc-probe",
        }
        if any(labels.get(key) != value for key, value in required_labels.items()):
            raise ContainmentError("image labels do not match the lock")
        if inspected.get("Digest") != expected["manifest_sha256"] or inspected["Id"] != image_value["local_image_id"]:
            raise ContainmentError("local image does not match the locked digest/ID")
        if f"{inspected['Os']}/{inspected['Architecture']}" != "linux/amd64" or inspected["Config"]["User"] != "65532:65532":
            raise ContainmentError("image platform or user identity is invalid")
        return Admission(repo, prerequisite, image_lock, sbom, image, inspected["Id"], image_value)
    except BaseException:
        prerequisite.close()
        if image_lock is not None:
            image_lock.close()
        if sbom is not None:
            sbom.close()
        raise


def unavailable_evidence(repo: Path, output: Path, *, image_lock_path: Path | None = None) -> dict[str, Any]:
    lock_path = image_lock_path or repo / "containers/prime-probe/image-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    value = {
        "status": "UNVERIFIED",
        "go_eligible": False,
        "launch_started": False,
        "cleanup_verified": False,
        "reason": f"containment not run because image status is {lock.get('status', 'UNKNOWN')}",
    }
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return value


class PodmanSandbox:
    def __init__(
        self,
        admission: Admission,
        *,
        relay_socket: Path | None = None,
        relay_hostname: str = "ollama.com",
        relay_port: int = 443,
    ):
        self.admission = admission
        self.relay_socket = relay_socket
        self.relay_hostname = relay_hostname
        self.relay_port = relay_port
        if relay_socket is not None and (relay_hostname, relay_port) not in {
            ("ollama.com", 443), ("127.0.0.1", 11434),
        }:
            raise ContainmentError("relay target is outside the exact Ollama allowlist")
        self.name = f"hermes-prime-phase01-{secrets.token_hex(6)}"
        self.container_id: str | None = None
        self.cgroup_path: Path | None = None
        self.relay_fd: int | None = None
        self.relay_identity: tuple[int, int, int, int, int, int, int] | None = None

    def _revalidate_relay(self) -> None:
        if self.relay_socket is None or self.relay_fd is None or self.relay_identity is None:
            return
        fd_stat = os.fstat(self.relay_fd)
        path_stat = os.stat(self.relay_socket, follow_symlinks=False)
        fd_identity = (fd_stat.st_uid, fd_stat.st_gid, fd_stat.st_mode, fd_stat.st_dev, fd_stat.st_ino, fd_stat.st_size, fd_stat.st_mtime_ns)
        path_identity = (path_stat.st_uid, path_stat.st_gid, path_stat.st_mode, path_stat.st_dev, path_stat.st_ino, path_stat.st_size, path_stat.st_mtime_ns)
        if fd_identity != self.relay_identity or path_identity != self.relay_identity:
            raise ContainmentError("relay socket replaced after admission")

    def create(self, command: Sequence[str]) -> str:
        self.admission.revalidate()
        arguments = [
            "podman", "create", "--name", self.name, "--pull=never", "--read-only",
            "--user", "65532:65532", "--network", "none", "--pid", "private",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", "768m", "--cpus", "1",
            "--env", "PRIME_AGENT_CODING_AGENT_DIR=/tmp/.prime/agent",
            "--env", "PRIME_AGENT_SESSION_DIR=/tmp/.prime/agent/sessions",
            "--tmpfs", "/work:rw,nosuid,nodev,noexec,size=67108864",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=33554432",
            "--tmpfs", "/run:rw,nosuid,nodev,noexec,size=16777216",
        ]
        relay_fd: int | None = None
        if self.relay_socket is not None:
            socket_path = self.relay_socket.resolve(strict=True)
            if not socket_path.is_socket() or "/mnt/d/" in str(socket_path) or "/.hermes/" in str(socket_path):
                raise ContainmentError("relay mount must be one live non-workspace Unix socket")
            relay_fd = os.open(socket_path, getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISSOCK(os.fstat(relay_fd).st_mode):
                os.close(relay_fd)
                raise ContainmentError("relay descriptor is not a Unix socket")
            self.relay_fd = relay_fd
            relay_stat = os.fstat(relay_fd)
            self.relay_identity = (relay_stat.st_uid, relay_stat.st_gid, relay_stat.st_mode, relay_stat.st_dev, relay_stat.st_ino, relay_stat.st_size, relay_stat.st_mtime_ns)
            if self.relay_port == 443:
                arguments += [
                    "--sysctl", "net.ipv4.ip_unprivileged_port_start=0",
                    "--add-host", "ollama.com:127.0.0.1",
                ]
            arguments += ["--mount", f"type=bind,src={socket_path},target=/run/prime-relay/relay.sock,ro=true"]
        arguments += ["--entrypoint", "/usr/local/bin/python", self.admission.image, "-c", command[0], *command[1:]]
        try:
            self._revalidate_relay()
            self.container_id = _run(arguments).stdout.strip()
            self._revalidate_relay()
            self._assert_created_contract()
            self.admission.revalidate()
            return self.container_id
        except BaseException:
            if self.container_id is not None:
                _run(["podman", "rm", "--force", self.container_id], check=False)
            self.container_id = None
            if self.relay_fd is not None:
                os.close(self.relay_fd)
                self.relay_fd = None
            raise

    def _assert_created_contract(self) -> None:
        if self.container_id is None:
            raise ContainmentError("container was not created")
        value = _podman_json("inspect", self.container_id)[0]
        host = value["HostConfig"]
        if value["Image"] != self.admission.image_id or value["Config"]["User"] != "65532:65532":
            raise ContainmentError("created container uses the wrong image or user")
        if host["ReadonlyRootfs"] is not True or host["NetworkMode"] != "none" or host["PidMode"] not in ("", "private"):
            raise ContainmentError("rootfs/network/PID isolation is invalid")
        expected_caps = set(_podman_json("info", "--format", "json")["host"]["security"]["capabilities"].split(","))
        if set(host["CapDrop"]) != expected_caps or host["CapAdd"] or host["SecurityOpt"] != ["no-new-privileges"]:
            raise ContainmentError("capability/no-new-privileges isolation is invalid")
        if host["PidsLimit"] != 64 or host["Memory"] != 805306368 or host["NanoCpus"] != 1_000_000_000:
            raise ContainmentError("resource bounds are invalid")
        environment = set(value["Config"].get("Env", []))
        expected_state = {
            "PRIME_AGENT_CODING_AGENT_DIR=/tmp/.prime/agent",
            "PRIME_AGENT_SESSION_DIR=/tmp/.prime/agent/sessions",
        }
        if not expected_state.issubset(environment):
            raise ContainmentError("transient Prime state is not tmpfs-bound")
        mounts = value.get("Mounts", [])
        if self.relay_socket is None and mounts:
            raise ContainmentError("unexpected host mount")
        if self.relay_socket is not None:
            if len(mounts) != 1 or mounts[0]["Destination"] != "/run/prime-relay/relay.sock" or mounts[0]["RW"] is not False:
                raise ContainmentError("relay is not the sole read-only host mount")
            create_command = value.get("Config", {}).get("CreateCommand", [])
            low_port = any(
                create_command[index:index + 2] == ["--sysctl", "net.ipv4.ip_unprivileged_port_start=0"]
                for index in range(max(0, len(create_command) - 1))
            )
            if self.relay_port == 443:
                if not low_port or host.get("ExtraHosts") != ["ollama.com:127.0.0.1"]:
                    raise ContainmentError("cloud relay loopback mapping is not exact")
            elif low_port or host.get("ExtraHosts"):
                raise ContainmentError("local relay has unnecessary hostname or low-port expansion")
        forbidden = ("/mnt/d", "/.hermes", "/profiles")
        if any(any(token in str(mount.get("Source", "")) for token in forbidden) for mount in mounts):
            raise ContainmentError("workspace/profile host mount detected")

    def start(self) -> None:
        if self.container_id is None:
            raise ContainmentError("container is not created")
        _run(["podman", "start", self.container_id])
        self._revalidate_relay()
        value = _podman_json("inspect", self.container_id)[0]
        pid = int(value["State"]["Pid"])
        if pid <= 0:
            raise ContainmentError("container did not start")
        cgroup_relative = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").strip().split("::", 1)[1]
        self.cgroup_path = Path("/sys/fs/cgroup") / cgroup_relative.lstrip("/")
        if not (self.cgroup_path / "cgroup.events").exists():
            raise ContainmentError("container cgroup.events is unavailable")

    def exec_python(self, code: str, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if self.container_id is None:
            raise ContainmentError("container is not created")
        return _run(["podman", "exec", self.container_id, "/usr/local/bin/python", "-c", code, *arguments], check=check)

    def teardown(self, *, timeout: float = 8.0) -> dict[str, Any]:
        if self.container_id is None:
            return {"cleanup_verified": True, "container_absent": True, "cgroup_populated": 0}
        _run(["podman", "stop", "--time", "1", self.container_id], check=False)
        _run(["podman", "kill", self.container_id], check=False)
        deadline = time.monotonic() + timeout
        populated: int | None = None
        while time.monotonic() < deadline:
            if self.cgroup_path is None or not (self.cgroup_path / "cgroup.events").exists():
                populated = 0
                break
            events = dict(line.split() for line in (self.cgroup_path / "cgroup.events").read_text(encoding="utf-8").splitlines())
            populated = int(events["populated"])
            if populated == 0:
                break
            time.sleep(0.05)
        if populated != 0:
            raise ContainmentError("container cgroup did not become empty")
        _run(["podman", "rm", "--force", self.container_id], check=False)
        absent = _run(["podman", "inspect", self.container_id], check=False).returncode != 0
        if self.relay_fd is not None:
            os.close(self.relay_fd)
            self.relay_fd = None
        self.admission.revalidate()
        if not absent:
            raise ContainmentError("container remains after teardown")
        return {"cleanup_verified": True, "container_absent": True, "cgroup_populated": 0}

    def __enter__(self) -> "PodmanSandbox":
        return self

    def __exit__(self, *_: object) -> None:
        self.teardown()
