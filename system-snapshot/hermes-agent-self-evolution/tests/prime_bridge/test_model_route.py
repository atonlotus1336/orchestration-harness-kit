from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from prime_bridge.model_route import (
    PRIME_RUNTIME_CREDENTIAL,
    SCHEMA,
    resolve_hermes_provider_credential,
    resolve_model_route,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "model_routes"
NOW = dt.datetime(2026, 8, 11, 20, 0, tzinfo=dt.timezone.utc)


def load(name: str) -> dict:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    raw["api"] = raw.pop("api_mode", "openai-completions")
    raw.update({
        "contract_version": "1.0",
        "active_profile": "home",
        "authority": {
            "source_path": "__WINDOWS_WORKSPACE__/.hermes/profiles/home/config.yaml",
            "source_sha256": "a" * 64,
        },
        "captured_at": "2026-08-11T19:55:00Z",
        "run_id": "route-12345678",
    })
    raw["compatibility"]["verified_at"] = "2026-08-11T19:55:00Z"
    return raw


@pytest.mark.parametrize("_review_finding", [None], ids=["CR-01"])
def test_canonical_schema_and_resolver_reject_contract_drift(_review_finding: None) -> None:
    policy = load("ollama_cloud.json")
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(policy)
    assert resolve_model_route(policy, now=NOW).ready

    drifted = copy.deepcopy(policy)
    drifted["api_mode"] = drifted.pop("api")
    assert list(Draft202012Validator(SCHEMA, format_checker=FormatChecker()).iter_errors(drifted))
    assert not resolve_model_route(drifted, now=NOW).ready


@pytest.mark.parametrize("fixture", ["local_ollama.json", "ollama_cloud.json"])
def test_schema_and_resolver_accept_identical_authority_bound_routes(fixture: str) -> None:
    policy = load(fixture)
    original = copy.deepcopy(policy)
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(policy)
    decision = resolve_model_route(policy, now=NOW)
    assert decision.ready
    assert decision.prime_config["baseUrl"] == policy["base_url"]
    assert decision.prime_config["models"][0]["id"] == policy["model"]
    assert decision.prime_config["authority"] == policy["authority"]
    assert policy == original


def test_ollama_cloud_projects_shared_hermes_provider_without_secret_material() -> None:
    policy = load("ollama_cloud.json")
    policy["credential"] = {"source": "hermes-provider-config", "provider_id": "ollama-cloud"}
    decision = resolve_model_route(policy, now=NOW)

    assert decision.ready
    assert decision.prime_config["apiKey"] == {
        "source": "hermes-provider-config",
        "providerId": "ollama-cloud",
    }
    assert "api_key" not in json.dumps(decision.prime_config)


def test_local_ollama_service_projects_runtime_only_placeholder() -> None:
    policy = load("local_ollama.json")
    policy["credential"] = {
        "source": "runtime-environment",
        "environment_variable": "HERMES_PRIME_LOCAL_PLACEHOLDER",
    }
    decision = resolve_model_route(policy, now=NOW)
    assert decision.ready
    assert decision.prime_config["apiKey"] == {
        "source": "runtime-environment",
        "environmentVariable": "HERMES_PRIME_LOCAL_PLACEHOLDER",
    }


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(api_mode=value.pop("api")),
    lambda value: value.update(extra=True),
    lambda value: value["compatibility"].update(context_window=True),
    lambda value: value["authority"].update(source_sha256="A" * 64),
])
def test_aliases_extra_keys_boolean_integers_and_bad_digests_fail(mutation) -> None:
    policy = load("ollama_cloud.json")
    mutation(policy)
    assert not resolve_model_route(policy, now=NOW).ready


@pytest.mark.parametrize("timestamp", ["2026-08-11T19:00:00Z", "2026-08-11T19:55:00", "2026-08-11T20:02:00Z"])
def test_stale_naive_and_future_evidence_fail(timestamp: str) -> None:
    policy = load("ollama_cloud.json")
    policy["captured_at"] = timestamp
    assert not resolve_model_route(policy, now=NOW).ready


@pytest.mark.parametrize("port", [8642, 9119])
def test_agent_endpoints_are_rejected(port: int) -> None:
    policy = load("local_ollama.json")
    policy["base_url"] = f"http://127.0.0.1:{port}/v1"
    assert "gateway/dashboard" in resolve_model_route(policy, now=NOW).reason


def test_literal_secret_and_unsupported_api_fail_at_schema_boundary() -> None:
    policy = load("local_ollama.json")
    policy["api_key"] = "sentinel-" + "secret-value"
    assert not resolve_model_route(policy, now=NOW).ready
    policy = load("local_ollama.json")
    policy["api"] = "anthropic-messages"
    assert not resolve_model_route(policy, now=NOW).ready


def test_explicit_fallback_is_validated_but_never_selected() -> None:
    policy = load("ollama_cloud.json")
    fallback = load("local_ollama.json")
    policy["explicit_fallback"] = {
        key: fallback[key]
        for key in ("provider", "model", "base_url", "api", "compatibility", "credential")
    }
    decision = resolve_model_route(policy, now=NOW)
    assert decision.ready
    assert decision.prime_config["models"][0]["id"] == policy["model"]
    assert decision.explicit_fallback["models"][0]["id"] == fallback["model"]


@pytest.mark.parametrize("_review_finding", [None], ids=["WR-02"])
def test_integrity_patterns_are_exact_in_bridge_and_capability_schemas(_review_finding: None) -> None:
    root = Path(__file__).resolve().parents[2]
    bridge = json.loads((root / "contracts" / "prime-bridge.schema.json").read_text(encoding="utf-8"))
    capabilities = json.loads((root / "contracts" / "prime-capabilities.schema.json").read_text(encoding="utf-8"))
    assert bridge["properties"]["prime_source"]["properties"]["integrity"]["pattern"] == "^(?:git:sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})$"
    assert capabilities["properties"]["runtime"]["properties"]["integrity"]["pattern"] == "^(?:git:sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})$"


@pytest.mark.parametrize("_review_finding", [None], ids=["WR-04"])
def test_boolean_integer_and_weak_timestamp_are_rejected(_review_finding: None) -> None:
    boolean_integer = load("ollama_cloud.json")
    boolean_integer["compatibility"]["context_window"] = True
    assert not resolve_model_route(boolean_integer, now=NOW).ready

    naive_timestamp = load("ollama_cloud.json")
    naive_timestamp["captured_at"] = "2026-08-11T19:55:00"
    assert not resolve_model_route(naive_timestamp, now=NOW).ready


def _write_shared_provider(config: Path, secret: str) -> None:
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        yaml.safe_dump({"providers": {"ollama-cloud": {"api_key": "ollama", "models": ["glm-5.2:cloud"]}}}),
        encoding="utf-8",
    )
    config.with_name(".env").write_text(f"OLLAMA_API_KEY={secret}\n", encoding="utf-8")
    config.with_name("auth.json").write_text(
        json.dumps({"credential_pool": {"ollama-cloud": [{"auth_type": "api_key", "source": "env:OLLAMA_API_KEY"}]}}),
        encoding="utf-8",
    )


def test_shared_provider_resolution_exposes_only_public_status_and_fingerprint(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    secret = "sentinel-hermes-shared-provider-secret"
    _write_shared_provider(config, secret)
    resolved = resolve_hermes_provider_credential("ollama-cloud", config)
    public = resolved.public_evidence()

    assert resolved.available and resolved.secret == secret
    assert public["source_reference"] == "OLLAMA_API_KEY"
    assert public["runtime_reference"] == PRIME_RUNTIME_CREDENTIAL
    assert public["availability_status"] == "VERIFIED"
    assert public["reachability_status"] == "NOT_RUN"
    assert public["value_persisted"] is False
    assert json.dumps(secret) not in json.dumps(public, sort_keys=True)


def test_shared_provider_public_fingerprint_ignores_secret_rotation(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    _write_shared_provider(config, "sentinel-hermes-shared-provider-secret")
    first = resolve_hermes_provider_credential("ollama-cloud", config)
    _write_shared_provider(config, "rotated-sentinel-hermes-provider-secret")
    second = resolve_hermes_provider_credential("ollama-cloud", config)

    assert first.config_fingerprint_sha256 == second.config_fingerprint_sha256
    assert first.secret != second.secret


def test_custom_provider_auth_alias_resolves_shared_hermes_credential(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    secret = "sentinel-custom-provider-secret"
    config.write_text(
        yaml.safe_dump({"providers": {"ollama-cloud-local": {"models": ["deepseek-v4-flash:cloud"]}}}),
        encoding="utf-8",
    )
    config.with_name(".env").write_text(f"OLLAMA_API_KEY={secret}\n", encoding="utf-8")
    config.with_name("auth.json").write_text(json.dumps({
        "credential_pool": {
            "custom:ollama-cloud-local": [{"auth_type": "api_key", "source": "env:OLLAMA_API_KEY"}],
        },
    }), encoding="utf-8")
    resolved = resolve_hermes_provider_credential("ollama-cloud-local", config)
    assert resolved.available and resolved.secret == secret
    assert resolved.public_evidence()["source_reference"] == "OLLAMA_API_KEY"
