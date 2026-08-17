from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import tarfile
import tempfile
import io

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
PROBE = ROOT / "containers" / "prime-probe"
BUILD_PATH = PROBE / "build_image.py"


def load_build_module():
    spec = importlib.util.spec_from_file_location("phase01_prime_build", BUILD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prerequisite_and_dependency_locks_are_exact() -> None:
    lock = json.loads((PROBE / "prime-source.lock.json").read_text(encoding="utf-8"))
    assert lock["commit"] == "95afd319a78ae017a41241d50b013d656a0685ce"
    assert lock["tree"] == "caf6fa88a48d2a4b07b903c327ae3d6b2d1b0466"
    assert "@sha256:" in lock["node_base"] and ":22" not in lock["node_base"]
    assert "@sha256:" in lock["python_base"] and ":3.12" not in lock["python_base"]
    provenance = ROOT / lock["provenance_path"]
    prerequisite = ROOT / lock["podman_prerequisite_path"]
    assert hashlib.sha256(provenance.read_bytes()).hexdigest() == lock["provenance_sha256"]
    assert hashlib.sha256(prerequisite.read_bytes()).hexdigest() == lock["podman_prerequisite_sha256"]
    requirements = (PROBE / "requirements.lock").read_text(encoding="utf-8")
    required_kernel_packages = (
        "beautifulsoup4",
        "dill",
        "httpx",
        "ipykernel",
        "lxml",
        "nest-asyncio",
        "numpy",
        "pandas",
        "pydantic",
        "python-dotenv",
        "pyyaml",
        "requests",
        "scipy",
        "tomli",
        "tyro",
    )
    assert all(f"{package}==" in requirements for package in required_kernel_packages)
    requirements_input = (PROBE / "requirements.in").read_text(encoding="utf-8")
    assert all(package in requirements_input for package in required_kernel_packages)
    assert "hatchling==1.27.0" in requirements
    assert "--hash=sha256:" in requirements
    assert " @ " not in requirements and "http://" not in requirements


def test_provenance_fd_consumer_retains_and_revalidates_every_member() -> None:
    module = load_build_module()
    with module.SourceFdConsumer.from_repository(ROOT) as consumer:
        assert len(consumer.entries) > 50
        consumer.add_build_inputs(PROBE)
        before = consumer.snapshot()
        stream = consumer.deterministic_context_bytes(PROBE)
        with tarfile.open(fileobj=io.BytesIO(stream), mode="r:") as archive:
            names = archive.getnames()
        assert "Containerfile" in names
        assert "requirements.in" in names
        assert "prime/package-lock.json" in names
        assert consumer.snapshot() == before
        consumer.revalidate()
        assert all(entry.fd >= 0 for entry in consumer.entries)
    assert all(entry.fd == -1 for entry in consumer.entries)


def test_toctou_replacement_is_rejected_without_reopening() -> None:
    module = load_build_module()
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        source = root / "source.txt"
        source.write_bytes(b"original")
        record = module.identity_for_path(source, "source.txt")
        entry = module.FdEntry.open_verified(source, record)
        replacement = root / "replacement.txt"
        replacement.write_bytes(b"replacement")
        os.replace(replacement, source)
        try:
            assert entry.read_all() == b"original"
            with pytest.raises(module.IntegrityError):
                entry.revalidate_path_and_handle()
        finally:
            entry.close()


def test_wsl_device_reenumeration_preserves_stable_provenance_and_live_toctou(tmp_path: pathlib.Path) -> None:
    module = load_build_module()
    source = tmp_path / "source.txt"
    source.write_bytes(b"immutable-source")
    record = module.identity_for_path(source, "source.txt")
    record["device"] += 1  # DrvFS st_dev is boot-local and changes after WSL restarts.
    entry = module.FdEntry.open_verified(source, record)
    try:
        entry.revalidate_handle()
        entry.revalidate_path_and_handle()
        replacement = tmp_path / "replacement.txt"
        replacement.write_bytes(b"replacement")
        os.replace(replacement, source)
        with pytest.raises(module.IntegrityError, match="path replacement"):
            entry.revalidate_path_and_handle()
    finally:
        entry.close()


def test_unavailable_prerequisite_writes_not_run_without_podman(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    module = load_build_module()
    prerequisite = tmp_path / "prerequisite.json"
    prerequisite.write_text(json.dumps({"status": "UNAVAILABLE", "go_eligible": False, "reason": "test unavailable"}), encoding="utf-8")
    output = tmp_path / "image-lock.json"
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Podman must not run")

    monkeypatch.setattr(module.subprocess, "run", forbidden)
    result = module.dispatch(ROOT, prerequisite, output, build=False)
    assert result["status"] == "NOT_RUN"
    assert result["go_eligible"] is False
    assert called is False
    assert not (tmp_path / "sbom.spdx.json").exists()


def test_deterministic_fd_context_is_byte_identical() -> None:
    module = load_build_module()
    with module.SourceFdConsumer.from_repository(ROOT) as consumer:
        first = consumer.deterministic_context_bytes(PROBE)
        consumer.revalidate()
        second = consumer.deterministic_context_bytes(PROBE)
        assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()


def test_image_lock_and_sbom_are_bound_when_ready() -> None:
    image_lock = PROBE / "image-lock.json"
    sbom = PROBE / "sbom.spdx.json"
    if not image_lock.exists():
        pytest.fail("READY Plan 05 must create image-lock.json")
    value = json.loads(image_lock.read_text(encoding="utf-8"))
    assert value["status"] == "READY"
    assert value["go_eligible"] is True
    assert "@sha256:" in value["runnable_image"]
    assert ":phase01" not in value["runnable_image"]
    assert sbom.exists()
    assert hashlib.sha256(sbom.read_bytes()).hexdigest() == value["sbom_sha256"]
    inventory = json.loads(sbom.read_text(encoding="utf-8"))
    package_ids = {package["SPDXID"] for package in inventory["packages"]}
    assert any(identifier.startswith("SPDXRef-npm-") for identifier in package_ids)
    assert any(identifier.startswith("SPDXRef-pypi-") for identifier in package_ids)
    assert any(package["name"] == "prime-agent-runtime" for package in inventory["packages"])
    assert len(inventory["packages"]) > 100


def test_reproducible_build_receipt_and_relay_contract() -> None:
    image_lock = json.loads((PROBE / "image-lock.json").read_text(encoding="utf-8"))
    assert image_lock["canonical_reproducibility"] is True
    assert image_lock["builds"][0]["application_manifest_sha256"] == image_lock["builds"][1]["application_manifest_sha256"]
    assert image_lock["builds"][0]["runtime_config_sha256"] == image_lock["builds"][1]["runtime_config_sha256"]
    assert image_lock["builds"][0]["base_layer_digests"] == image_lock["builds"][1]["base_layer_digests"]
    assert image_lock["builds"][0]["platform"] == "linux/amd64"
    assert image_lock["builds"][0]["application_file_count"] > 39000
    for build in image_lock["builds"]:
        assert build["labels"]["org.opencontainers.image.revision"] == image_lock["source_commit"]
        assert build["labels"]["ai.__PRIVATE_TOKEN_02__.prime.tree"] == image_lock["source_tree"]
        assert build["labels"]["ai.__PRIVATE_TOKEN_02__.prime.context.sha256"] == image_lock["source_context_sha256"]
    relay_schema = json.loads((PROBE / "relay-config.schema.json").read_text(encoding="utf-8"))
    assert relay_schema["properties"]["listen_host"]["const"] == "127.0.0.1"
    assert relay_schema["properties"]["allowed_port"]["const"] == 443


def test_no_build_launch_contract_uses_immutable_digest_only() -> None:
    containerfile = (PROBE / "Containerfile").read_text(encoding="utf-8")
    assert "latest" not in containerfile
    assert "curl |" not in containerfile
    build_source = BUILD_PATH.read_text(encoding="utf-8")
    assert "docker " not in build_source.lower()
    assert "direct-process" not in build_source.lower()


def test_containment_source_has_no_docker_or_direct_process_fallback() -> None:
    source = (ROOT / "prime_bridge" / "sandbox.py").read_text(encoding="utf-8")
    assert "docker" not in source.lower()
    assert "subprocess.Popen" not in source
    for required in (
        '"--pull=never"', '"--read-only"', '"--network", "none"',
        '"--cap-drop", "ALL"', '"no-new-privileges"', '"--pids-limit", "64"',
    ):
        assert required in source


def test_junit_gate_rejects_zero_tests_skips_and_failures(tmp_path: pathlib.Path) -> None:
    from prime_bridge.sandbox import ContainmentError, validate_junit_counts

    clean = tmp_path / "clean.xml"
    clean.write_text('<testsuite tests="8" failures="0" errors="0" skipped="0"/>', encoding="utf-8")
    assert validate_junit_counts(clean) == {"tests": 8, "failures": 0, "errors": 0, "skipped": 0}

    for name, attributes in (
        ("empty", 'tests="0" failures="0" errors="0" skipped="0"'),
        ("skipped", 'tests="8" failures="0" errors="0" skipped="1"'),
        ("failed", 'tests="8" failures="1" errors="0" skipped="0"'),
        ("errored", 'tests="8" failures="0" errors="1" skipped="0"'),
    ):
        report = tmp_path / f"{name}.xml"
        report.write_text(f"<testsuite {attributes}/>", encoding="utf-8")
        with pytest.raises(ContainmentError):
            validate_junit_counts(report)
