
import json
from pathlib import Path

import pytest
import subprocess

from prime_bridge.adaptive_review.herdr_client import (
    HerdrAmbiguousDelivery, HerdrClient, HerdrSessionRef,
)
from prime_bridge.adaptive_review.herdr_transport import (
    HerdrReviewTransport, validate_herdr_result,
)
from prime_bridge.adaptive_review.transport import BudgetViolation, ReviewBudgetTracker


def limits():
    return {
        "max_context_bytes": 20000,
        "max_model_calls": 1,
        "max_estimated_input_tokens": 20000,
        "max_tool_calls": 0,
        "max_elapsed_seconds": 60,
        "max_output_tokens": 256,
        "max_total_tokens": 20000,
    }


def test_real_client_owns_named_server_lifecycle(tmp_path, monkeypatch):
    calls = []

    class Process:
        def poll(self):
            return None

        def wait(self, timeout):
            return 0

        def terminate(self):
            calls.append(("terminate",))

    def popen(argv, **_kwargs):
        calls.append(tuple(argv))
        return Process()

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        if "status" in argv:
            return subprocess.CompletedProcess(
                argv, 0,
                stdout='{"result":{"running":true,"session_name":"eval-test"}}',
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 0, stdout='{"result":{}}', stderr="")

    executable = tmp_path / "herdr.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(subprocess, "Popen", popen)
    client = HerdrClient(executable, session="eval-test", runner=runner)
    client.start_server(cwd=tmp_path, env={"HERDR_CONFIG_PATH": str(tmp_path / "config.toml")})
    client.close_session()
    assert any(call[-1:] == ("server",) for call in calls)
    assert any("stop" in call and "eval-test" in call for call in calls)
    assert any("delete" in call and "eval-test" in call for call in calls)


def configured(tmp_path, *, client_factory):
    exe = tmp_path / "herdr.exe"
    exe.write_bytes(b"fixture")
    launcher = tmp_path / "launcher.py"
    launcher.write_text("fixture")
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({
        "schema_version": "1.0", "schema_sha256": "a" * 64,
        "runnable": True,
    }))
    config = tmp_path / "adapters.json"
    config.write_text(json.dumps({"schema_version": "1.0", "mode": "supervised"}))
    repository = tmp_path / "repository"
    repository.mkdir()
    return HerdrReviewTransport(
        executable=exe, runtime_lock=lock, adapter_config=config,
        repository=repository, launcher=launcher,
        reviewer_command=("reviewer-fixture",),
        receipt_id="receipt-1", attempt_id="attempt-1",
        reviewer_id="correctness", s_level="S1",
        source_identity="snapshot-1", client_factory=client_factory,
    )


class SuccessfulClient:
    def __init__(self, *args, **kwargs):
        self.root = None
        self.closed = []

    def create_workspace(self, *, cwd, label, env):
        self.root = Path(cwd)
        assert env["HERDR_CONFIG_PATH"].startswith(str(cwd))
        return HerdrSessionRef("session", "workspace-1", "pane-1")

    def run_fixed_launcher(self, pane_id, argv):
        request = json.loads((self.root / "request.json").read_text())
        result = {
            "schema_version": "1.0",
            "receipt_id": request["receipt_id"],
            "attempt_id": request["attempt_id"],
            "reviewer_id": request["reviewer_id"],
            "packet_sha256": request["packet_sha256"],
            "source_identity": request["source_identity"],
            "findings": [],
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            "started_at": "2026-09-12T10:00:00Z",
            "finished_at": "2026-09-12T10:00:01Z",
        }
        (self.root / "result.json").write_text(json.dumps(result))
        return {"result": {"accepted": True}}

    def wait_agent(self, target, *, timeout_ms):
        return {"result": {"status": "done"}}

    def observe_agent(self, target):
        return {"result": {"status": "done"}}

    def close_workspace(self, workspace_id):
        self.closed.append(workspace_id)


def test_transport_completes_from_closed_file_result_without_terminal_scraping(tmp_path):
    clients = []
    def factory(*args, **kwargs):
        client = SuccessfulClient(*args, **kwargs)
        clients.append(client)
        return client
    transport = configured(tmp_path, client_factory=factory)
    tracker = ReviewBudgetTracker(limits())
    packet = transport.payload("rubric", {"src/app.py": "print('ok')"}, 256)
    tracker.admitted_packets = (packet,)
    assert transport.run("rubric", {"src/app.py": "print('ok')"}, tracker) == ()
    assert tracker.public()["usage_complete"] is True
    assert tracker.public()["model_requests"] == 1
    assert clients[0].closed == ["workspace-1"]
    assert clients[0].root.exists() is False


def test_ambiguous_delivery_is_not_replayed_and_leaves_usage_unsettled(tmp_path):
    class Ambiguous(SuccessfulClient):
        calls = 0
        def run_fixed_launcher(self, pane_id, argv):
            type(self).calls += 1
            raise HerdrAmbiguousDelivery("unknown")
    transport = configured(tmp_path, client_factory=Ambiguous)
    tracker = ReviewBudgetTracker(limits())
    packet = transport.payload("rubric", {"src/app.py": "print('ok')"}, 256)
    tracker.admitted_packets = (packet,)
    with pytest.raises(BudgetViolation, match="held"):
        transport.run("rubric", {"src/app.py": "print('ok')"}, tracker)
    assert Ambiguous.calls == 1
    assert tracker.public()["usage_complete"] is False
    assert tracker.public()["automatic_retries"] == 0


def test_result_rejects_lineage_usage_and_mutation_forgery():
    expected = {
        "receipt_id": "r", "attempt_id": "a", "reviewer_id": "v",
        "packet_sha256": "a" * 64, "source_identity": "s",
    }
    base = {
        "schema_version": "1.0", **expected, "findings": [],
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "started_at": "2026-09-12T10:00:00Z",
        "finished_at": "2026-09-12T10:00:01Z",
    }
    assert validate_herdr_result(base, expected) == ()
    forged = dict(base, receipt_id="other")
    with pytest.raises(BudgetViolation, match="lineage"):
        validate_herdr_result(forged, expected)
    usage = dict(base, usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3})
    with pytest.raises(BudgetViolation, match="inconsistent"):
        validate_herdr_result(usage, expected)
    mutation = dict(base, findings=[{"patch": "forbidden"}])
    with pytest.raises(BudgetViolation, match="mutation"):
        validate_herdr_result(mutation, expected)


def test_transport_clamps_aggregate_output_cap_to_bridge_maximum(tmp_path):
    transport = configured(tmp_path, client_factory=SuccessfulClient)

    packet = json.loads(transport.payload(
        "rubric", {"src/app.py": "print('ok')"}, 6144,
    ))

    assert packet["max_tokens"] == 2048
    assert packet["reserved_budget"]["max_output_tokens"] == 2048
