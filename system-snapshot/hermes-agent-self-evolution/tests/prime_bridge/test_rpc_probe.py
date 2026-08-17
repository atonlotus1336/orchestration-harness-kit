from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from prime_bridge.evidence_scan import named_credentials, prepare_evidence, scan_persisted_bytes
from prime_bridge.model_route import PRIME_RUNTIME_CREDENTIAL
from prime_bridge.rpc_probe import closed_probe_evidence, run_probe


ROOT = Path(__file__).resolve().parents[2]
FAKE = ROOT / "tests" / "fixtures" / "prime_rpc" / "fake_prime_rpc.py"
CONTRACT = ROOT / "tests" / "fixtures" / "prime_rpc" / "prime-v0.7.1-rpc-contract.json"
SCHEMA = ROOT / "contracts" / "prime-rpc-evidence.schema.json"
DOC = (
    ROOT / ".git" / "phase01-gap-lifecycle" / "sources" / "prime-agent"
    / "95afd319a78ae017a41241d50b013d656a0685ce"
    / "packages" / "coding-agent" / "docs" / "rpc.md"
)
EXPECTED_ROUTE = {
    "provider": "hermes-ollama",
    "model": "fixture-model",
    "api": "openai-completions",
    "endpoint": "https://fixture.invalid/v1",
}
TEST_CREDENTIAL = "sentinel-runtime-credential"
SHARED_CONFIG = {
    "provider_id": "hermes-ollama",
    "source_provider": "ollama-cloud",
    "baseUrl": "https://ollama.com/v1",
    "api": "openai-completions",
    "apiKey": {"source": "hermes-provider-config", "providerId": "ollama-cloud"},
    "models": [{
        "id": "glm-5.2:cloud",
        "contextWindow": 131072,
        "maxTokens": 8192,
        "supportsStreaming": True,
        "supportsTools": True,
    }],
    "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False},
    "activeProfile": "home",
}


def execute(mode: str, **kwargs):
    kwargs.setdefault("timeout_seconds", 2.0)
    kwargs.setdefault("environment", {"PRIME_TEST_API_KEY": TEST_CREDENTIAL})
    kwargs.setdefault("credential_environment", "PRIME_TEST_API_KEY")
    kwargs.setdefault("expected_route", EXPECTED_ROUTE)
    kwargs.setdefault("fixture_source", FAKE.read_text(encoding="utf-8"))
    kwargs.setdefault("fixture_mode", mode)
    return asyncio.run(run_probe(**kwargs))


def validate(value: dict) -> None:
    Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(value)


def test_pinned_contract_matches_official_prime_v071_document() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["commit"] == "95afd319a78ae017a41241d50b013d656a0685ce"
    assert hashlib.sha256(DOC.read_bytes()).hexdigest() == contract["document_sha256"]
    assert contract["invariants"] == {
        "record_delimiter": "LF",
        "optional_trailing_cr": True,
        "unicode_line_separators_are_content": True,
        "request_id_scope": "response-only",
        "prompt_response_precedes_active_events": True,
        "tool_correlation_fields": ["toolCallId", "toolName"],
        "active_session_start": "agent_start",
        "active_session_end": "agent_end",
    }


def test_success_is_one_contained_session_with_exact_route_tool_and_final_text() -> None:
    result = execute("success")
    assert result.passed and result.status == "READY"
    assert result.cleanup_verified and result.workspace_removed
    assert result.evidence["containment"]["cgroup_populated"] == 0
    assert result.evidence["route"] == {"status": "VERIFIED", **EXPECTED_ROUTE}
    assert result.evidence["rpc"]["request_count"] == 6
    assert result.evidence["rpc"]["agent_runs"] == 1
    assert result.evidence["rpc"]["ipython_calls"] == 1
    assert result.evidence["final_text"] == "PRIME_RPC_OK:4"
    assert result.final_state and result.final_state["sessionId"] == "fixture-session-01"
    assert result.usage and result.usage["toolCalls"] == result.usage["toolResults"] == 1
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert TEST_CREDENTIAL not in serialized
    assert "[REDACTED]" in serialized
    assert "\u2028" in serialized and "\u2029" in serialized
    assert result.evidence["scans"]["generic_scan"] == {"status": "PASS", "matches": 0}
    assert result.evidence["scans"]["exact_scan"]["status"] == "PASS"
    requests = [item["frame"] for item in result.transcript if item["direction"] == "request"]
    responses = [item["frame"] for item in result.transcript if item["frame"].get("type") == "response"]
    assert len({frame["id"] for frame in requests}) == len(requests) == 6
    assert {frame["id"] for frame in responses} == {frame["id"] for frame in requests}
    assert all("id" not in item["frame"] for item in result.transcript if item["frame"].get("type") != "response" and item["direction"] == "response")
    validate(result.to_dict())


@pytest.mark.parametrize("_review_finding", [None], ids=["WR-03"])
def test_crlf_is_accepted_without_changing_lf_record_semantics(_review_finding: None) -> None:
    result = execute("crlf")
    assert result.passed and result.cleanup_verified


@pytest.mark.parametrize(
    "mode",
    [
        "malformed",
        "bare_cr",
        "response_id",
        "event_id",
        "event_session_substitution",
        "session_substitution",
        "tool_id",
        "tool_name",
        "double_tool",
        "tool_error",
        "wrong_result",
        "wrong_final",
        "route_mismatch",
    ],
)
def test_protocol_adversaries_are_unverified_and_cgroup_empty(mode: str) -> None:
    result = execute(mode)
    assert result.status == "UNVERIFIED" and result.passed is False
    assert result.evidence["go_eligible"] is False
    assert result.evidence["rpc"]["status"] == "FAIL"
    assert result.cleanup_verified and result.workspace_removed
    assert result.evidence["containment"]["cgroup_populated"] == 0
    validate(result.to_dict())


@pytest.mark.parametrize("mode", ["api_unobservable", "endpoint_unobservable"])
def test_api_or_endpoint_unobservable_is_not_route_verified(mode: str) -> None:
    result = execute(mode)
    assert result.status == "UNVERIFIED"
    assert result.evidence["route"]["status"] == "UNVERIFIED"
    assert result.cleanup_verified


def test_stalled_stream_times_out_and_empties_cgroup() -> None:
    result = execute("stall", timeout_seconds=0.2)
    assert result.status == "UNVERIFIED" and result.reason == "rpc_timeout"
    assert result.cleanup_verified and result.workspace_removed


def test_unexpected_exit_is_unverified_and_empties_cgroup() -> None:
    result = execute("crash")
    assert result.status == "UNVERIFIED" and result.reason == "rpc_process_exited"
    assert result.cleanup_verified and result.workspace_removed


def test_cancellation_sends_abort_then_empties_cgroup() -> None:
    async def scenario():
        cancel = asyncio.Event()

        async def trigger() -> None:
            await asyncio.sleep(0.15)
            cancel.set()

        trigger_task = asyncio.create_task(trigger())
        result = await run_probe(
            timeout_seconds=2,
            cancel_event=cancel,
            environment={"PRIME_TEST_API_KEY": TEST_CREDENTIAL},
            credential_environment="PRIME_TEST_API_KEY",
            expected_route=EXPECTED_ROUTE,
            fixture_source=FAKE.read_text(encoding="utf-8"),
            fixture_mode="cancel",
        )
        await trigger_task
        return result

    result = asyncio.run(scenario())
    assert result.status == "UNVERIFIED" and result.reason == "rpc_cancelled"
    assert any(item["frame"].get("type") == "abort" for item in result.transcript)
    assert result.cleanup_verified and result.workspace_removed


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-03"])
def test_redaction_is_recursive_exact_longest_first_and_non_disclosing(_review_finding: None) -> None:
    short = "sentinel-secret"
    long = "sentinel-secret-with-suffix"
    prepared = prepare_evidence(
        {
            "nested": [long, {"exception": RuntimeError(f"failed with {short}")}],
            "authorization": f"Bearer {long}",
            "state": {"echo": f"prefix-{long}-suffix"},
        },
        (short, long, ""),
    )
    serialized = prepared.persisted_bytes.decode("utf-8")
    assert short not in serialized and long not in serialized
    assert "[REDACTED]" in serialized
    assert prepared.scans["exact_scan"] == {
        "status": "PASS",
        "values_supplied": 2,
        "raw_matches": 4,
        "persisted_matches": 0,
    }
    assert prepared.scans["generic_scan"] == {"status": "PASS", "matches": 0}


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-07"])
def test_provider_or_route_substitution_is_unverified(_review_finding: None) -> None:
    result = execute("route_mismatch")
    assert result.status == "UNVERIFIED"
    assert result.passed is False and result.evidence["go_eligible"] is False
    assert result.evidence["route"]["status"] == "UNVERIFIED"
    assert result.cleanup_verified and result.workspace_removed


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-09"])
def test_completion_marker_without_correlated_ipython_result_is_rejected(_review_finding: None) -> None:
    result = execute("wrong_result")
    assert result.status == "UNVERIFIED"
    assert result.passed is False and result.evidence["go_eligible"] is False
    assert result.evidence["rpc"]["status"] == "FAIL"
    assert result.cleanup_verified and result.workspace_removed


def test_secret_scan_always_runs_and_empty_values_are_not_applicable() -> None:
    assert named_credentials({"ONE": "", "TWO": "value-two"}, ("ONE",)) == ()
    prepared = prepare_evidence({"message": "safe", "token": "generic-secret-shape"})
    assert prepared.scans["exact_scan"]["status"] == "NOT_APPLICABLE"
    assert prepared.scans["generic_scan"] == {"status": "PASS", "matches": 0}
    assert scan_persisted_bytes(prepared.persisted_bytes)["generic_scan"]["status"] == "PASS"


def test_missing_credential_is_stable_closed_and_does_not_admit(monkeypatch: pytest.MonkeyPatch) -> None:
    from prime_bridge import rpc_probe

    def forbidden(*_args, **_kwargs):
        raise AssertionError("containment admission must not run without a credential")

    monkeypatch.setattr(rpc_probe, "admit", forbidden)
    first = asyncio.run(run_probe(
        environment={},
        credential_environment="OLLAMA_CLOUD_API_KEY",
        expected_route=EXPECTED_ROUTE,
        fixture_source=FAKE.read_text(encoding="utf-8"),
        fixture_mode="success",
    ))
    second = asyncio.run(run_probe(
        environment={"UNRELATED": "must-not-be-read"},
        credential_environment="OLLAMA_CLOUD_API_KEY",
        expected_route=EXPECTED_ROUTE,
        fixture_source=FAKE.read_text(encoding="utf-8"),
        fixture_mode="success",
    ))
    assert first.to_dict() == second.to_dict()
    assert first.status == "NOT_RUN" and first.evidence["launch_started"] is False
    assert first.evidence["credential"]["status"] == "UNVERIFIED"
    assert first.evidence["scans"]["exact_scan"]["status"] == "NOT_APPLICABLE"
    validate(first.to_dict())


def test_unavailable_containment_is_not_run_and_does_not_admit(monkeypatch: pytest.MonkeyPatch) -> None:
    from prime_bridge import rpc_probe

    monkeypatch.setattr(
        rpc_probe,
        "_containment_snapshot",
        lambda _repo: {"status": "UNAVAILABLE", "upstream_sha256": "b" * 64},
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("containment admission must not run when unavailable")

    monkeypatch.setattr(rpc_probe, "admit", forbidden)
    result = execute("success")
    assert result.status == "NOT_RUN" and result.evidence["rpc"]["status"] == "NOT_RUN"
    assert result.evidence["launch_started"] is False
    assert result.evidence["containment"]["container_absent"] is True
    assert "SKIP" not in json.dumps(result.to_dict()).upper()
    validate(result.to_dict())


def test_closed_helper_is_schema_valid_and_exact_scan_hides_value() -> None:
    value = closed_probe_evidence(
        reason_code="containment_unavailable",
        credential_environment="PRIME_TEST_API_KEY",
        environment={"PRIME_TEST_API_KEY": TEST_CREDENTIAL},
        containment_status="UNAVAILABLE",
        upstream_sha256="c" * 64,
        expected_route=EXPECTED_ROUTE,
    )
    assert TEST_CREDENTIAL not in json.dumps(value)
    assert value["scans"]["exact_scan"]["status"] == "PASS"
    assert value["credential"]["availability_status"] == "VERIFIED"
    assert value["credential"]["reachability_status"] == "NOT_RUN"
    assert value["credential"]["value_persisted"] is False
    validate(value)


def test_shared_hermes_provider_is_available_before_reachability_and_never_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from prime_bridge import rpc_probe

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "providers:\n  ollama-cloud:\n    api_key: ollama\n",
        encoding="utf-8",
    )
    config_path.with_name(".env").write_text(
        "OLLAMA_API_KEY=sentinel-shared-provider-value\n",
        encoding="utf-8",
    )
    config_path.with_name("auth.json").write_text(
        json.dumps({
            "credential_pool": {
                "ollama-cloud": [{"auth_type": "api_key", "source": "env:OLLAMA_API_KEY"}]
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        rpc_probe,
        "_containment_snapshot",
        lambda _repo: {"status": "UNAVAILABLE", "upstream_sha256": "b" * 64},
    )

    result = asyncio.run(run_probe(
        prime_config=SHARED_CONFIG,
        hermes_config_path=config_path,
        expected_route={
            "provider": "hermes-ollama",
            "model": "glm-5.2:cloud",
            "api": "openai-completions",
            "endpoint": "https://ollama.com/v1",
        },
    ))

    assert result.status == "NOT_RUN"
    assert result.evidence["credential"]["source"] == "hermes-provider-config"
    assert result.evidence["credential"]["availability_status"] == "VERIFIED"
    assert result.evidence["credential"]["reachability_status"] == "NOT_RUN"
    assert result.evidence["credential"]["runtime_reference"] == PRIME_RUNTIME_CREDENTIAL
    assert result.evidence["credential"]["value_persisted"] is False
    assert "sentinel-shared-provider-value" not in json.dumps(result.to_dict())
    validate(result.to_dict())


def test_prime_models_config_contains_only_runtime_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    from prime_bridge import rpc_probe

    captured: dict[str, object] = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["input"] = kwargs["input"]
        captured["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(arguments, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(rpc_probe.subprocess, "run", fake_run)
    rpc_probe._write_prime_config(
        "container-id",
        SHARED_CONFIG,
        PRIME_RUNTIME_CREDENTIAL,
        child_environment={},
    )

    payload = json.loads(captured["input"])
    assert payload["providers"]["hermes-ollama"]["apiKey"] == PRIME_RUNTIME_CREDENTIAL
    assert TEST_CREDENTIAL not in json.dumps(payload)
    assert captured["environment"] == {}


def test_live_cli_without_opt_in_is_not_run_not_skipped() -> None:
    environment = os.environ.copy()
    environment.pop("PRIME_LIVE_PROBE", None)
    completed = subprocess.run(
        [sys.executable, "-m", "prime_bridge.rpc_probe", "--live"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    value = json.loads(completed.stdout)
    assert value["status"] == "NOT_RUN"
    assert "SKIP" not in json.dumps(value).upper()
    validate(value)
