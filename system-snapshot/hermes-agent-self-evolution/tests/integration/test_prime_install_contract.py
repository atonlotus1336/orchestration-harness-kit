from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACQUIRE = ROOT / "scripts" / "acquire-prime-agent-source.sh"
CHECKER = ROOT / "scripts" / "check-prime-install.sh"
BASELINE = ROOT / "scripts" / "capture-prime-integration-baseline.sh"
COMMIT = "95afd319a78ae017a41241d50b013d656a0685ce"
CACHE = ROOT / ".git" / "phase01-gap-lifecycle" / "sources" / "prime-agent" / COMMIT


def run_checker(source: Path, output: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = ["bash", str(CHECKER), "--source", str(source)]
    if output:
        command.extend(["--output", str(output)])
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def test_acquisition_script_is_pinned_and_disables_execution_features() -> None:
    text = ACQUIRE.read_text(encoding="utf-8")
    for value in (
        "https://github.com/PrimeIntellect-ai/prime-agent.git",
        "v0.7.1",
        COMMIT,
        "caf6fa88a48d2a4b07b903c327ae3d6b2d1b0466",
        "39ee303bca10c0933cf917275613c8f44099f50de1650a5c356e7cda02b701e8",
        "core.hooksPath=/dev/null",
        "GIT_LFS_SKIP_SMUDGE=1",
        "submodule.recurse=false",
    ):
        assert value in text


def test_absent_source_is_missing(tmp_path: Path) -> None:
    result = run_checker(tmp_path / "missing")
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "MISSING"


def test_pinned_cache_emits_full_nonexecuting_identity(tmp_path: Path) -> None:
    output = tmp_path / "provenance.json"
    result = run_checker(CACHE, output)
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["status"] == "READY"
    record = payload["record"]
    assert record["commit"] == COMMIT
    assert record["members"]
    assert all({"uid", "gid", "mode", "device", "inode", "size", "mtime_ns", "sha256"} <= set(item) for item in record["members"])
    assert all(isinstance(item["mode"], int) for item in record["members"])
    assert record["consumer_reopen_contract"]["descriptor_lifetime"] == "process-local-only"
    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_wrong_marker_and_symlink_fail_without_execution(tmp_path: Path) -> None:
    wrong = tmp_path / "wrong"
    shutil.copytree(CACHE, wrong)
    marker = json.loads((wrong / ".prime-source.json").read_text(encoding="utf-8"))
    marker["commit"] = "0" * 40
    (wrong / ".prime-source.json").write_text(json.dumps(marker), encoding="utf-8")
    assert json.loads(run_checker(wrong).stdout)["status"] == "INCOMPATIBLE"

    aliased = tmp_path / "aliased"
    shutil.copytree(CACHE, aliased)
    (aliased / "forbidden-link").symlink_to("package.json")
    result = run_checker(aliased)
    assert result.returncode == 2
    assert "symlink is forbidden" in json.loads(result.stdout)["reason"]


def test_contract_schemas_reject_malformed_integrity() -> None:
    bridge = json.loads((ROOT / "contracts" / "prime-bridge.schema.json").read_text(encoding="utf-8"))
    capabilities = json.loads((ROOT / "contracts" / "prime-capabilities.schema.json").read_text(encoding="utf-8"))
    assert bridge["properties"]["prime_source"]["properties"]["integrity"]["pattern"].endswith("[0-9a-f]{64})$")
    assert capabilities["properties"]["runtime"]["properties"]["integrity"]["pattern"].endswith("[0-9a-f]{64})$")


@pytest.mark.parametrize("_review_finding", [None], ids=["WR-01"])
def test_nonexecutable_path_prime_is_never_used_as_install_or_baseline_evidence(
    tmp_path: Path, _review_finding: None,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "executed"
    fake_prime = fake_bin / "prime-agent"
    fake_prime.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    fake_prime.chmod(0o644)
    completed = subprocess.run(
        ["bash", str(BASELINE), "capture"],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["source_kind"] == "LIVE"
    assert marker.exists() is False
    assert "prime-agent" not in BASELINE.read_text(encoding="utf-8")
