from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import socket
import struct
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from prime_bridge.inference_relay import InferenceRelay, RelayPolicy, RelayPolicyError
from prime_bridge.sandbox import Admission, ContainmentError, PodmanSandbox, admit, unavailable_evidence
from tests.fixtures.prime_rpc.sandbox_adversaries import DETACHED_DESCENDANTS, IDENTITY_AND_DENIALS


ROOT = pathlib.Path(__file__).resolve().parents[2]
PREREQUISITE = ROOT / ".planning/phases/01-contracts-feasibility/evidence/phase01-podman-prerequisite.json"
SOURCE_LOCK = ROOT / "containers/prime-probe/prime-source.lock.json"
IMAGE_LOCK = ROOT / "containers/prime-probe/image-lock.json"
PREREQUISITE_SCHEMA = json.loads((ROOT / "contracts/phase01-podman-prerequisite.schema.json").read_text(encoding="utf-8"))

READY_NODES = (
    "tests/integration/test_prime_podman_sandbox.py::test_ready_admission_binds_exact_image_sbom_and_runtime",
    "tests/integration/test_prime_podman_sandbox.py::test_filesystem_network_identity_and_mount_denials[CR-02]",
    "tests/integration/test_prime_podman_sandbox.py::test_detached_descendants_are_cgroup_owned_and_teardown_is_empty[CR-08]",
    "tests/integration/test_prime_podman_sandbox.py::test_relay_replace_after_check_never_reaches_container",
    "tests/integration/test_prime_podman_sandbox.py::test_replace_after_check_is_rejected",
)
UNAVAILABLE_NODES = (
    "tests/integration/test_prime_podman_sandbox.py::test_unavailable_branch_is_named_not_run_without_podman",
    "tests/integration/test_prime_feasibility.py::test_complete_podman_unavailable_chain_is_closed_and_nonlaunching",
    "tests/prime_bridge/test_rpc_probe.py::test_unavailable_containment_is_not_run_and_does_not_admit",
    "tests/prime_bridge/test_sandbox.py::test_unavailable_prerequisite_writes_not_run_without_podman",
)


def _dispatch_environment_branch(
    prerequisite_path: pathlib.Path,
    source_lock_path: pathlib.Path,
    image_lock_path: pathlib.Path,
    junit_path: pathlib.Path,
) -> dict[str, object]:
    from prime_bridge.sandbox import ContainmentError, validate_junit_counts

    raw = prerequisite_path.read_bytes()
    prerequisite = json.loads(raw)
    Draft202012Validator(PREREQUISITE_SCHEMA, format_checker=FormatChecker()).validate(prerequisite)
    source_lock = json.loads(source_lock_path.read_text(encoding="utf-8"))
    prerequisite_sha256 = hashlib.sha256(raw).hexdigest()
    if source_lock.get("podman_prerequisite_sha256") != prerequisite_sha256:
        raise ContainmentError("prerequisite hash is not bound by the source lock")

    image = json.loads(image_lock_path.read_text(encoding="utf-8"))
    if prerequisite["status"] == "READY":
        if image.get("status") != "READY" or image.get("go_eligible") is not True:
            raise ContainmentError("READY prerequisite has mixed non-READY image evidence")
        if image.get("prerequisite_sha256") != prerequisite_sha256:
            raise ContainmentError("READY image is not bound to the prerequisite")
        nodes = READY_NODES
    else:
        if image.get("status") != "NOT_RUN" or image.get("go_eligible") is not False:
            raise ContainmentError("UNAVAILABLE prerequisite has mixed launch-capable image evidence")
        if image.get("upstream_sha256") != prerequisite_sha256:
            raise ContainmentError("NOT_RUN image is not bound to the prerequisite")
        nodes = UNAVAILABLE_NODES

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *nodes, f"--junitxml={junit_path}"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if completed.returncode != 0:
        raise ContainmentError(f"branch pytest failed: {completed.stdout}\n{completed.stderr}")
    counts = validate_junit_counts(junit_path)
    names = {item.attrib["name"] for item in ET.parse(junit_path).getroot().iter("testcase")}
    expected_names = {node.rsplit("::", 1)[-1] for node in nodes}
    if names != expected_names or counts["tests"] != len(nodes):
        raise ContainmentError(f"required branch controls were omitted: expected={expected_names}, actual={names}")
    return {"status": prerequisite["status"], "counts": counts, "nodes": sorted(names)}


@pytest.fixture
def admission() -> Admission:
    value = admit(ROOT)
    try:
        yield value
    finally:
        value.close()


def test_ready_admission_binds_exact_image_sbom_and_runtime(admission: Admission) -> None:
    assert "@sha256:" in admission.image
    assert admission.sbom.sha256 == admission.image_value["sbom_sha256"]
    assert admission.image_value["runtime_contract"] == {"pull": False, "rebuild": False, "mutable_tag": False}
    admission.revalidate()


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-02"])
def test_filesystem_network_identity_and_mount_denials(admission: Admission, _review_finding: None) -> None:
    sandbox = PodmanSandbox(admission)
    sandbox.create(["import time; time.sleep(60)"])
    try:
        sandbox.start()
        result = json.loads(sandbox.exec_python(IDENTITY_AND_DENIALS).stdout)
        assert result == {
            "uid": 65532, "gid": 65532, "network_denied": True,
            "root_writable": False, "work_writable": True,
            "mnt_g_exists": False, "hermes_profile_exists": False,
            "no_new_privs": "1", "cap_eff": "0000000000000000",
        }
    finally:
        receipt = sandbox.teardown()
    assert receipt == {"cleanup_verified": True, "container_absent": True, "cgroup_populated": 0}


def test_unavailable_branch_is_named_not_run_without_podman(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    image_lock = tmp_path / "image-lock.json"
    image_lock.write_text(json.dumps({"status": "NOT_RUN"}), encoding="utf-8")
    monkeypatch.setattr("prime_bridge.sandbox._run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Podman must not run")))
    result = unavailable_evidence(ROOT, tmp_path / "containment.json", image_lock_path=image_lock)
    assert result["status"] == "UNVERIFIED"
    assert result["go_eligible"] is False and result["launch_started"] is False


def test_relay_policy_rejects_endpoint_and_dns_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = RelayPolicy("ollama.com", ("192.0.2.1",))
    monkeypatch.setattr(RelayPolicy, "resolve", classmethod(lambda cls, hostname: RelayPolicy(hostname, ("192.0.2.2",))))
    with pytest.raises(RelayPolicyError, match="drift"):
        policy.assert_dns_unchanged()


def test_relay_policy_allows_only_exact_ollama_cloud_or_loopback_targets() -> None:
    assert RelayPolicy("127.0.0.1", ("127.0.0.1",), 11434).port == 11434
    for hostname, ips, port in [
        ("127.0.0.1", ("127.0.0.1",), 443),
        ("localhost", ("127.0.0.1",), 11434),
        ("ollama.com", ("127.0.0.1",), 443),
    ]:
        with pytest.raises(RelayPolicyError):
            RelayPolicy(hostname, ips, port)


def test_endpoint_pinned_raw_relay_accepts_only_exact_header(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    async def scenario() -> None:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(await reader.read(4096))
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]

        async def connector(_ip: str, _port: int):
            return await asyncio.open_connection("127.0.0.1", upstream_port)

        monkeypatch.setattr(RelayPolicy, "assert_dns_unchanged", lambda self: None)
        relay = InferenceRelay(tmp_path / "relay.sock", RelayPolicy("ollama.com", ("192.0.2.1",)), connector=connector)
        async with relay:
            reader, writer = await asyncio.open_unix_connection(str(relay.socket_path))
            hostname = b"ollama.com"
            writer.write(struct.pack("!H", len(hostname)) + hostname + struct.pack("!H", 443) + b"raw-tls-record")
            await writer.drain()
            assert await reader.readexactly(len(b"raw-tls-record")) == b"raw-tls-record"
            writer.close()
            await writer.wait_closed()

            _reader, wrong = await asyncio.open_unix_connection(str(relay.socket_path))
            hostname = b"example.com"
            wrong.write(struct.pack("!H", len(hostname)) + hostname + struct.pack("!H", 443))
            await wrong.drain()
            wrong.close()
            await wrong.wait_closed()
            await asyncio.sleep(0.05)
            assert relay.accepted == 1 and relay.rejected == 1
        upstream.close()
        await upstream.wait_closed()

    asyncio.run(scenario())


def test_network_none_container_uses_only_fixed_uds_relay(admission: Admission, tmp_path: pathlib.Path) -> None:
    socket_path = tmp_path / "relay.sock"
    received: dict[str, object] = {}
    ready = threading.Event()

    def server() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            socket_path.chmod(0o666)
            listener.listen(1)
            ready.set()
            connection, _ = listener.accept()
            with connection:
                size = struct.unpack("!H", connection.recv(2))[0]
                received["hostname"] = connection.recv(size).decode("ascii")
                received["port"] = struct.unpack("!H", connection.recv(2))[0]
                payload = connection.recv(4096)
                received["payload"] = payload
                connection.sendall(payload)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    assert ready.wait(2)
    sandbox = PodmanSandbox(admission, relay_socket=socket_path)
    sandbox.create(["import time; time.sleep(60)"])
    try:
        sandbox.start()
        code = r'''
import json, socket, subprocess, time
config = {"listen_host":"127.0.0.1","listen_port":18443,"uds_path":"/run/prime-relay/relay.sock","allowed_hostname":"ollama.com","allowed_port":443}
open("/tmp/relay.json", "w").write(json.dumps(config))
process = subprocess.Popen(["python", "/opt/phase01/relay_client.py", "--config", "/tmp/relay.json"])
try:
    for _ in range(50):
        try:
            client = socket.create_connection(("127.0.0.1", 18443), timeout=.2); break
        except OSError: time.sleep(.05)
    else: raise RuntimeError("relay client did not start")
    client.sendall(b"raw-tls-record")
    print(client.recv(4096).decode("ascii"))
finally:
    process.terminate(); process.wait(timeout=2)
'''
        assert sandbox.exec_python(code).stdout.strip() == "raw-tls-record"
    finally:
        receipt = sandbox.teardown()
    thread.join(timeout=2)
    assert received == {"hostname": "ollama.com", "port": 443, "payload": b"raw-tls-record"}
    assert receipt["cleanup_verified"] is True


def test_network_none_container_relays_only_to_local_ollama(admission: Admission, tmp_path: pathlib.Path) -> None:
    socket_path = tmp_path / "local-ollama.sock"
    received: dict[str, object] = {}
    ready = threading.Event()

    def server() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            socket_path.chmod(0o666)
            listener.listen(1)
            ready.set()
            connection, _ = listener.accept()
            with connection:
                size = struct.unpack("!H", connection.recv(2))[0]
                received["hostname"] = connection.recv(size).decode("ascii")
                received["port"] = struct.unpack("!H", connection.recv(2))[0]
                received["payload"] = connection.recv(4096)
                connection.sendall(received["payload"])

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    assert ready.wait(2)
    sandbox = PodmanSandbox(
        admission, relay_socket=socket_path,
        relay_hostname="127.0.0.1", relay_port=11434,
    )
    config = {
        "listen_host": "127.0.0.1", "listen_port": 11434,
        "uds_path": "/run/prime-relay/relay.sock",
        "allowed_hostname": "127.0.0.1", "allowed_port": 11434,
    }
    command = (
        "import asyncio,runpy; ns=runpy.run_path('/opt/phase01/relay_client.py'); "
        f"asyncio.run(ns['serve']({config!r}))"
    )
    sandbox.create([command])
    try:
        sandbox.start()
        code = r'''
import socket, time
for _ in range(50):
    try:
        client = socket.create_connection(("127.0.0.1", 11434), timeout=.2); break
    except OSError: time.sleep(.05)
else: raise RuntimeError("local Ollama relay client did not start")
client.sendall(b"local-ollama-only")
print(client.recv(4096).decode("ascii"))
'''
        assert sandbox.exec_python(code).stdout.strip() == "local-ollama-only"
    finally:
        receipt = sandbox.teardown()
    thread.join(timeout=2)
    assert received == {"hostname": "127.0.0.1", "port": 11434, "payload": b"local-ollama-only"}
    assert receipt["cleanup_verified"] is True


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-08"])
def test_detached_descendants_are_cgroup_owned_and_teardown_is_empty(admission: Admission, _review_finding: None) -> None:
    sandbox = PodmanSandbox(admission)
    sandbox.create([DETACHED_DESCENDANTS])
    try:
        sandbox.start()
        assert sandbox.cgroup_path is not None
        deadline = time.monotonic() + 5
        process_count = 0
        while time.monotonic() < deadline:
            process_count = len((sandbox.cgroup_path / "cgroup.procs").read_text().splitlines())
            if process_count >= 3:
                break
            time.sleep(0.05)
        assert process_count >= 3
    finally:
        receipt = sandbox.teardown()
    assert receipt["cgroup_populated"] == 0 and receipt["container_absent"] is True


def test_exception_path_still_empties_cgroup_and_removes_container(admission: Admission) -> None:
    sandbox = PodmanSandbox(admission)
    sandbox.create([DETACHED_DESCENDANTS])
    container_id = sandbox.container_id
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with sandbox:
            sandbox.start()
            raise RuntimeError("synthetic failure")
    assert container_id is not None
    assert subprocess.run(["podman", "inspect", container_id], capture_output=True).returncode != 0


def test_relay_replace_after_check_never_reaches_container(monkeypatch: pytest.MonkeyPatch, admission: Admission, tmp_path: pathlib.Path) -> None:
    socket_path = tmp_path / "relay.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    socket_path.chmod(0o666)
    sandbox = PodmanSandbox(admission, relay_socket=socket_path)
    from prime_bridge import sandbox as sandbox_module
    real_run = sandbox_module._run
    create_seen = False

    def replacing_run(arguments, **kwargs):
        nonlocal create_seen
        if list(arguments[:2]) == ["podman", "create"]:
            create_seen = True
            listener.close()
            socket_path.unlink()
            replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            replacement.bind(str(socket_path))
            replacement.close()
            return subprocess.CompletedProcess(arguments, 0, stdout="not-a-real-container\n", stderr="")
        if list(arguments[:2]) == ["podman", "rm"]:
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        return real_run(arguments, **kwargs)

    monkeypatch.setattr(sandbox_module, "_run", replacing_run)
    with pytest.raises(ContainmentError, match="relay socket replaced"):
        sandbox.create(["raise AssertionError('must not execute')"])
    assert create_seen is True and sandbox.container_id is None


def test_replace_after_check_is_rejected(tmp_path: pathlib.Path) -> None:
    from prime_bridge.sandbox import VerifiedArtifact
    original = tmp_path / "artifact.json"
    original.write_bytes(b"original")
    artifact = VerifiedArtifact.open(original)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"replacement")
    replacement.replace(original)
    try:
        with pytest.raises(ContainmentError, match="replaced"):
            artifact.revalidate()
    finally:
        artifact.close()


def test_status_dispatcher_runs_current_hash_bound_branch_with_zero_skips(tmp_path: pathlib.Path) -> None:
    result = _dispatch_environment_branch(PREREQUISITE, SOURCE_LOCK, IMAGE_LOCK, tmp_path / "branch.xml")
    assert result["status"] in {"READY", "UNAVAILABLE"}
    assert result["counts"]["tests"] == len(result["nodes"])
    assert result["counts"]["skipped"] == 0


def test_status_dispatcher_executes_unavailable_closure_and_rejects_mixed_evidence(tmp_path: pathlib.Path) -> None:
    unavailable = json.loads(PREREQUISITE.read_text(encoding="utf-8"))
    unavailable.update({"status": "UNAVAILABLE", "go_eligible": False, "reason": "test prerequisite unavailable"})
    unavailable["capabilities"] = None
    unavailable["root_receipt_sha256"] = None
    prerequisite_path = tmp_path / "prerequisite.json"
    prerequisite_path.write_text(json.dumps(unavailable, sort_keys=True), encoding="utf-8")
    prerequisite_sha256 = hashlib.sha256(prerequisite_path.read_bytes()).hexdigest()

    source_lock = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
    source_lock["podman_prerequisite_sha256"] = prerequisite_sha256
    source_lock_path = tmp_path / "source-lock.json"
    source_lock_path.write_text(json.dumps(source_lock), encoding="utf-8")

    closed_image = tmp_path / "image-lock.json"
    closed_image.write_text(json.dumps({
        "status": "NOT_RUN", "go_eligible": False, "upstream_sha256": prerequisite_sha256,
    }), encoding="utf-8")
    result = _dispatch_environment_branch(
        prerequisite_path, source_lock_path, closed_image, tmp_path / "unavailable.xml",
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["counts"] == {"tests": len(UNAVAILABLE_NODES), "failures": 0, "errors": 0, "skipped": 0}

    with pytest.raises(ContainmentError, match="mixed"):
        _dispatch_environment_branch(
            prerequisite_path, source_lock_path, IMAGE_LOCK, tmp_path / "mixed.xml",
        )
