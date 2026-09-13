
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "probe_herdr_evaluator", ROOT / "scripts" / "probe-herdr-evaluator.py"
)
probe_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe_module)

METHODS = [
    "workspace.create", "pane.create", "agent.create",
    "agent.prompt", "agent.wait", "event.subscribe",
]
SCHEMA = {"methods": [{"name": name} for name in METHODS]}


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()


def lock(tmp_path, *, schema_sha256=None, methods=METHODS, active=False):
    value = {
        "schema_version": "1.0",
        "activation_state": "observe" if active else "inactive",
        "release": "0.9.0",
        "source_commit": "9ad65d9031e8cb16a7b553c0e6f74809e9811e92",
        "expected_version_output": "herdr 0.9.0",
        "schema_sha256": schema_sha256,
        "required_methods": list(methods),
        "executables": {},
        "runnable": active,
        "note": "test",
    }
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def executable(tmp_path):
    path = tmp_path / "herdr.exe"
    path.write_bytes(b"fixture")
    return path


def runner_for(schema=SCHEMA, *, version="herdr 0.9.0", schema_code=0):
    def run(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, version + "\n", "")
        return subprocess.CompletedProcess(command, schema_code, json.dumps(schema), "")
    return run


def test_subprocess_output_is_decoded_as_utf8(tmp_path):
    seen = []

    def run(command, **kwargs):
        seen.append(kwargs)
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "herdr 0.9.0\n", "")
        schema = {"description": "UTF-8 punctuation: \u2014", "methods": [{"name": name} for name in METHODS]}
        return subprocess.CompletedProcess(command, 0, json.dumps(schema, ensure_ascii=False), "")

    schema = {"description": "UTF-8 punctuation: \u2014", "methods": [{"name": name} for name in METHODS]}
    result = probe_module.probe(
        lock(tmp_path, schema_sha256=digest(schema)),
        executable(tmp_path),
        runner=run,
    )

    assert result["status"] == "READY"
    assert all(call["encoding"] == "utf-8" for call in seen)
    assert all(call["errors"] == "strict" for call in seen)


def test_missing_executable_is_unavailable(tmp_path):
    result = probe_module.probe(lock(tmp_path), tmp_path / "missing.exe")
    assert result == {
        "status": "UNAVAILABLE", "runnable": False,
        "reason": "EXECUTABLE_NOT_FOUND",
    }


def test_unpinned_schema_is_observed_but_cannot_run(tmp_path):
    result = probe_module.probe(
        lock(tmp_path), executable(tmp_path), runner=runner_for()
    )
    assert result["status"] == "DEGRADED"
    assert result["reason"] == "SCHEMA_DIGEST_UNPINNED"
    assert result["runnable"] is False
    assert result["schema_sha256"] == digest(SCHEMA)


def test_matching_schema_can_qualify_while_inactive(tmp_path):
    result = probe_module.probe(
        lock(tmp_path, schema_sha256=digest(SCHEMA)),
        executable(tmp_path),
        runner=runner_for(),
    )
    assert result["status"] == "READY"
    assert result["reason"] == "QUALIFIED_INACTIVE"
    assert result["runnable"] is False


@pytest.mark.parametrize(
    "schema_hash, schema, reason",
    [
        ("0" * 64, SCHEMA, "SCHEMA_DIGEST_MISMATCH"),
        (None, {"methods": [{"name": "workspace.create"}]}, "SCHEMA_DIGEST_UNPINNED"),
    ],
)
def test_schema_pin_failures_never_become_runnable(tmp_path, schema_hash, schema, reason):
    result = probe_module.probe(
        lock(tmp_path, schema_sha256=schema_hash),
        executable(tmp_path),
        runner=runner_for(schema),
    )
    assert result["reason"] == reason
    assert result["runnable"] is False


def test_missing_required_method_is_unavailable(tmp_path):
    schema = {"methods": [{"name": name} for name in METHODS[:-1]]}
    result = probe_module.probe(
        lock(tmp_path, schema_sha256=digest(schema)),
        executable(tmp_path),
        runner=runner_for(schema),
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "REQUIRED_METHODS_MISSING"
    assert result["missing_method_count"] == 1


def test_malformed_schema_and_timeout_fail_closed(tmp_path):
    exe = executable(tmp_path)
    path = lock(tmp_path, schema_sha256="0" * 64)

    def malformed(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "herdr 0.9.0\n", "")
        return subprocess.CompletedProcess(command, 0, "not-json", "")

    assert probe_module.probe(path, exe, runner=malformed)["reason"] == "SCHEMA_UNREADABLE"

    def timeout(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "herdr 0.9.0\n", "")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    assert probe_module.probe(path, exe, runner=timeout)["reason"] == "SCHEMA_PROBE_TIMEOUT"
