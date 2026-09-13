"""Provider-free Herdr evaluator qualification matrix.

No test in this module starts Herdr or invokes a reviewer/provider. The transport
cases use an in-process control-plane double that writes only the closed result
file in an isolated temporary directory.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from prime_bridge.adaptive_review.herdr_client import (
    HerdrClientError,
    HerdrSessionRef,
)
from prime_bridge.adaptive_review.herdr_transport import (
    HerdrReviewTransport,
    validate_herdr_result,
)
from prime_bridge.adaptive_review.transport import (
    BudgetViolation,
    JsonReviewTransport,
    ReviewBudgetTracker,
)
from prime_bridge.adaptive_review.transport_registry import (
    is_admitted_report_only_transport,
)
from prime_bridge.harness_registry import UNIVERSAL_HARNESS_REGISTRY
from prime_bridge.orchestration_switch import operation_permitted

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HARNESSES = (
    "codex", "hermes", "claude-code", "moa", "cursor",
    "dsh", "pi", "opencode", "antigravity",
)


def _limits():
    return {
        "max_context_bytes": 50_000,
        "max_model_calls": 1,
        "max_estimated_input_tokens": 50_000,
        "max_tool_calls": 0,
        "max_elapsed_seconds": 300,
        "max_output_tokens": 256,
        "max_total_tokens": 50_000,
    }


def _fixture_transport(tmp_path, repository, level, client_factory):
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / f"herdr-{level}.exe"
    executable.write_bytes(b"provider-free-fixture")
    launcher = tmp_path / "fixed-launcher.py"
    launcher.write_text("# fixture", encoding="utf-8")
    lock = tmp_path / "runtime-lock.json"
    lock.write_text(json.dumps({
        "schema_version": "1.0",
        "schema_sha256": "a" * 64,
        "runnable": True,
    }), encoding="utf-8")
    config = tmp_path / "adapters.json"
    config.write_text(json.dumps({
        "schema_version": "1.0",
        "mode": "supervised",
    }), encoding="utf-8")
    return HerdrReviewTransport(
        executable=executable,
        runtime_lock=lock,
        adapter_config=config,
        repository=repository,
        launcher=launcher,
        reviewer_command=("provider-free-reviewer-double",),
        receipt_id=f"receipt-{level}",
        attempt_id=f"attempt-{level}",
        reviewer_id=f"reviewer-{level}",
        s_level=level,
        source_identity="snapshot-qualified",
        client_factory=client_factory,
    )


class FileResultClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.root = None
        self.launch_count = 0
        self.closed = False
        type(self).instances.append(self)

    def create_workspace(self, *, cwd, label, env):
        self.root = Path(cwd)
        assert self.root.is_relative_to(Path(cwd))
        assert env["HERDR_CONFIG_PATH"].startswith(str(self.root))
        assert "OPENAI_API_KEY" not in env
        return HerdrSessionRef(label, f"workspace-{label}", f"pane-{label}")

    def run_fixed_launcher(self, pane_id, argv):
        self.launch_count += 1
        request = json.loads((self.root / "request.json").read_text(encoding="utf-8"))
        result = {
            "schema_version": "1.0",
            **{key: request[key] for key in (
                "receipt_id", "attempt_id", "reviewer_id",
                "packet_sha256", "source_identity",
            )},
            "findings": [],
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 3,
                "total_tokens": 14,
            },
            "started_at": "2026-09-12T10:00:00Z",
            "finished_at": "2026-09-12T10:00:01Z",
        }
        (self.root / "result.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        return {"result": {"accepted": True}}

    def wait_agent(self, target, *, timeout_ms):
        # A late wait event is deliberately treated as an observation problem,
        # not permission to redeliver the reviewer request.
        raise HerdrClientError("event arrived after result")

    def observe_agent(self, target):
        return {"result": {"status": "done"}}

    def close_workspace(self, workspace_id):
        self.closed = True


def _run_level(root, repository, level):
    transport = _fixture_transport(root / level, repository, level, FileResultClient)
    tracker = ReviewBudgetTracker(_limits())
    contents = {"src/value.py": "def value():\n    return 42\n"}
    packet = transport.payload("Report correctness findings only.", contents, 256)
    tracker.admitted_packets = (packet,)
    assert transport.run("Report correctness findings only.", contents, tracker) == ()
    public = tracker.public()
    return level, public["input_units"], public["output_units"], public["total_units"]




def test_client_ignores_top_level_request_id_when_binding_workspace(tmp_path):
    from prime_bridge.adaptive_review.herdr_client import HerdrClient

    def runner(command, **kwargs):
        value = {
            "id": "cli:workspace:create",
            "result": {"root_pane": {
                "workspace_id": "w1", "pane_id": "w1:p1",
            }},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    executable = tmp_path / "herdr.exe"
    executable.write_bytes(b"fixture")
    client = HerdrClient(executable, session="qualification", runner=runner)
    ref = client.create_workspace(cwd=tmp_path, label="fixture", env={})
    assert ref.workspace_id == "w1"
    assert ref.pane_id == "w1:p1"

def test_client_sends_one_quoted_command_without_subprocess_shell(tmp_path):
    from prime_bridge.adaptive_review.herdr_client import HerdrClient

    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"result": {"accepted": True}}), ""
        )

    executable = tmp_path / "herdr.exe"
    executable.write_bytes(b"fixture")
    client = HerdrClient(executable, session="qualification", runner=runner)
    argv = (r"C:\Program Files\Python\python.exe", r"C:\review root\launcher.py", "--flag")
    client.run_fixed_launcher("pane-1", argv)
    command, kwargs = calls[0]
    pane_index = command.index("pane")
    assert command[pane_index:pane_index + 3] == ["pane", "run", "pane-1"]
    assert len(command[pane_index + 3:]) == 1
    assert "Program Files" in command[-1] and "review root" in command[-1]
    assert "shell" not in kwargs


def test_client_accepts_empty_success_from_pane_run(tmp_path):
    """Herdr 0.9.0 acknowledges a successful pane run with rc=0 and no stdout."""
    from prime_bridge.adaptive_review.herdr_client import HerdrClient

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    executable = tmp_path / "herdr.exe"
    executable.write_bytes(b"fixture")
    client = HerdrClient(executable, session="qualification", runner=runner)
    assert client.run_fixed_launcher("pane-1", ("reviewer", "--sealed")) == {
        "result": {"accepted": True}
    }

def test_shipped_runtime_is_pinned_but_disconnected_and_inactive():
    lock = json.loads((ROOT / "config/herdr-runtime-lock.json").read_text(encoding="utf-8"))
    adapters = json.loads((ROOT / "config/herdr-evaluator-adapters.json").read_text(encoding="utf-8"))
    assert lock["release"] == "0.9.0"
    assert lock["source_commit"] == "9ad65d9031e8cb16a7b553c0e6f74809e9811e92"
    assert lock["schema_sha256"] == "7ae4593be31915e2cd851c61fe59687556a2ebaaad4142fba5a9edbc9bafe7d9"
    assert lock["activation_state"] == "inactive"
    assert lock["runnable"] is False
    launcher = Path(r"__WINDOWS_WORKSPACE__/herdr/herdr.cmd") if os.name == "nt" else Path("__WSL_WORKSPACE__/herdr/herdr.cmd")
    assert launcher.is_file()
    assert adapters["mode"] == "off"
    assert adapters["reviewers"] == {}
    assert adapters["targets"]["windows"]["qualified"] is True
    assert adapters["targets"]["wsl"] == {"executable": None, "qualified": False}


def test_all_harnesses_share_one_inactive_transport_neutral_contract():
    registry = json.loads((ROOT / "config/evaluator-harness-adapters.json").read_text(encoding="utf-8"))
    assert UNIVERSAL_HARNESS_REGISTRY == EXPECTED_HARNESSES
    assert tuple(registry["adapters"]) == EXPECTED_HARNESSES
    assert registry["activation_state"] == "inactive"
    assert all("transport" not in value for value in registry["adapters"].values())


def test_s0_stays_deterministic_and_json_remains_default(tmp_path):
    assert is_admitted_report_only_transport(
        JsonReviewTransport("http://127.0.0.1:1/v1/chat/completions", "fixture")
    )
    with pytest.raises(BudgetViolation, match="invalid Herdr review level"):
        (tmp_path / "repo-s0").mkdir()
        transport = _fixture_transport(
            tmp_path / "herdr-s0", tmp_path / "repo-s0",
            "S0", FileResultClient,
        )
        transport.validate()


def test_parallel_s1_s4_exact_accounting_event_recovery_and_source_immutability(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "source.py"
    source.write_text("VALUE = 42\n", encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    FileResultClient.instances.clear()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda level: _run_level(tmp_path / "isolates", repository, level),
            ("S1", "S2", "S3", "S4"),
        ))
    assert results == [
        ("S1", 11, 3, 14), ("S2", 11, 3, 14),
        ("S3", 11, 3, 14), ("S4", 11, 3, 14),
    ]
    assert all(client.launch_count == 1 and client.closed for client in FileResultClient.instances)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_malformed_malicious_drifted_and_inconsistent_results_fail_closed():
    expected = {
        "receipt_id": "receipt", "attempt_id": "attempt",
        "reviewer_id": "reviewer", "packet_sha256": "a" * 64,
        "source_identity": "snapshot",
    }
    base = {
        "schema_version": "1.0", **expected, "findings": [],
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        "started_at": "2026-09-12T10:00:00Z",
        "finished_at": "2026-09-12T10:00:01Z",
    }
    variants = [
        dict(base, source_identity="drifted"),
        dict(base, finish_reason="unknown"),
        dict(base, findings=[{"commands": ["git apply"]}]),
        dict(base, usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 9}),
    ]
    for value in variants:
        with pytest.raises(BudgetViolation):
            validate_herdr_result(value, expected)


@pytest.mark.parametrize("mode,allowed", [
    ("off", False), ("observe", False), ("supervised", True), ("full", True),
])
def test_switch_mode_start_semantics_are_fail_closed(tmp_path, mode, allowed):
    from prime_bridge.orchestration_switch import OrchestrationSwitch
    switch = OrchestrationSwitch(tmp_path)
    if mode != "off":
        switch.set("herdr-review", mode, ("codex",))
    assert operation_permitted("herdr-review", "codex", "start", switch.path) is allowed
