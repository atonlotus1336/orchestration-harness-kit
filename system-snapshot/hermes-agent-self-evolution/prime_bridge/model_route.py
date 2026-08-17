"""Schema-first projection of G-managed Hermes model policy into Prime config."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "prime-model-route.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
HERMES_AGENT_PORTS = {8642, 9119}
MAX_EVIDENCE_AGE = dt.timedelta(minutes=15)
MAX_FUTURE_SKEW = dt.timedelta(minutes=1)
PRIME_RUNTIME_CREDENTIAL = "HERMES_PRIME_RUNTIME_API_KEY"
PLACEHOLDERS = {"ollama", "dummy", "placeholder", "none", "null", "changeme"}


@dataclasses.dataclass(frozen=True)
class ProviderCredential:
    provider_id: str
    source_reference: str | None
    config_fingerprint_sha256: str
    availability_status: str
    reason: str
    secret: str | None = dataclasses.field(default=None, repr=False, compare=False)

    @property
    def available(self) -> bool:
        return self.availability_status == "VERIFIED" and bool(self.secret)

    def public_evidence(self, *, reachability_status: str = "NOT_RUN") -> dict[str, Any]:
        if reachability_status not in {"NOT_RUN", "VERIFIED", "UNVERIFIED", "FAIL"}:
            raise ValueError("invalid provider reachability status")
        return {
            "source": "hermes-provider-config",
            "provider_id": self.provider_id,
            "source_reference": self.source_reference,
            "runtime_reference": PRIME_RUNTIME_CREDENTIAL,
            "config_fingerprint_sha256": self.config_fingerprint_sha256,
            "availability_status": self.availability_status,
            "reachability_status": reachability_status,
            "value_present": self.available,
            "value_persisted": False,
        }


def _provider_fingerprint(
    provider_id: str,
    provider: Mapping[str, Any] | None,
    credential_source: str | None,
    credential_configured: bool,
) -> str:
    value = {
        "schema_version": "1.0",
        "provider_id": provider_id,
        "provider_present": provider is not None,
        "public_keys": sorted(
            str(key)
            for key in (provider or {})
            if str(key).lower() not in {"api_key", "apikey", "token", "secret", "password", "credential"}
        ),
        "credential_field": "api_key",
        "credential_source": credential_source,
        "credential_configured": credential_configured,
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_env(path: Path, name: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    values = [line.split("=", 1)[1].strip() for line in lines if line.startswith(f"{name}=")]
    return values[-1] if values and values[-1] else None


def _credential_source(provider_id: str, auth_path: Path) -> str | None:
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    pool = raw.get("credential_pool") if isinstance(raw, Mapping) else None
    entries = pool.get(provider_id) if isinstance(pool, Mapping) else None
    if not isinstance(entries, list) and isinstance(pool, Mapping) and not provider_id.startswith("custom:"):
        entries = pool.get(f"custom:{provider_id}")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("auth_type") != "api_key":
            continue
        source = entry.get("source")
        if isinstance(source, str) and source.startswith("env:"):
            reference = source.removeprefix("env:")
            if reference and reference.replace("_", "").isalnum() and reference.upper() == reference:
                return reference
    return None


def resolve_hermes_provider_credential(
    provider_id: str,
    config_path: Path,
    *,
    environment: Mapping[str, str] | None = None,
    env_path: Path | None = None,
    auth_path: Path | None = None,
) -> ProviderCredential:
    """Follow Hermes' credential-pool source into its active profile environment."""

    if not provider_id or not isinstance(provider_id, str):
        raise ValueError("provider_id must be a non-empty string")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return ProviderCredential(
            provider_id=provider_id,
            source_reference=None,
            config_fingerprint_sha256=_provider_fingerprint(provider_id, None, None, False),
            availability_status="UNVERIFIED",
            reason="hermes_provider_config_unavailable",
        )
    providers = raw.get("providers") if isinstance(raw, Mapping) else None
    candidate = providers.get(provider_id) if isinstance(providers, Mapping) else None
    provider = candidate if isinstance(candidate, Mapping) else None
    reference = _credential_source(provider_id, auth_path or config_path.with_name("auth.json"))
    runtime = os.environ if environment is None else environment
    secret = runtime.get(reference, "") if reference is not None else ""
    if not secret and reference is not None:
        secret = _read_env(env_path or config_path.with_name(".env"), reference) or ""
    if not secret and reference is not None and len(config_path.parents) >= 3:
        secret = _read_env(config_path.parents[2] / ".env", reference) or ""
    if secret.lower() in PLACEHOLDERS:
        secret = ""
    fingerprint = _provider_fingerprint(provider_id, provider, f"env:{reference}" if reference else None, bool(secret))
    if provider is None or reference is None or not secret:
        return ProviderCredential(
            provider_id=provider_id,
            source_reference=reference,
            config_fingerprint_sha256=fingerprint,
            availability_status="UNVERIFIED",
            reason="hermes_provider_credential_chain_unavailable",
        )
    return ProviderCredential(
        provider_id=provider_id,
        source_reference=reference,
        config_fingerprint_sha256=fingerprint,
        availability_status="VERIFIED",
        reason="hermes_provider_credential_resolved",
        secret=secret,
    )


def active_profile_config(active_profile: str, *, home: Path | None = None) -> Path:
    if not active_profile or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in active_profile):
        raise ValueError("active_profile is not canonical")
    return (home or Path.home()) / ".hermes" / "profiles" / active_profile / "config.yaml"


@dataclasses.dataclass(frozen=True)
class RouteDecision:
    status: str
    reason: str
    prime_config: dict[str, Any] | None = None
    explicit_fallback: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _failure(reason: str) -> RouteDecision:
    return RouteDecision(status="INCOMPATIBLE", reason=reason)


def _timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(dt.timezone.utc)


def _semantic_error(route: Mapping[str, Any], now: dt.datetime) -> str | None:
    for field in ("captured_at",):
        try:
            value = _timestamp(str(route[field]))
        except (KeyError, TypeError, ValueError):
            return f"{field} must be timezone-aware"
        if value > now + MAX_FUTURE_SKEW or now - value > MAX_EVIDENCE_AGE:
            return f"{field} is stale or future-dated"
    try:
        verified = _timestamp(str(route["compatibility"]["verified_at"]))
    except (KeyError, TypeError, ValueError):
        return "compatibility.verified_at must be timezone-aware"
    if verified > now + MAX_FUTURE_SKEW or now - verified > MAX_EVIDENCE_AGE:
        return "compatibility.verified_at is stale or future-dated"

    parsed = urlsplit(str(route["base_url"]))
    try:
        port = parsed.port
    except ValueError:
        return "base_url contains an invalid port"
    if port in HERMES_AGENT_PORTS:
        return "Hermes gateway/dashboard endpoints cannot be model endpoints"
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "base_url must not contain credentials, query parameters, or fragments"
    return None


def _project(route: Mapping[str, Any]) -> dict[str, Any]:
    compatibility = route["compatibility"]
    credential = route["credential"]
    if credential["source"] == "hermes-provider-config":
        api_key = {
            "source": "hermes-provider-config",
            "providerId": credential["provider_id"],
        }
    else:
        api_key = {
            "source": "runtime-environment",
            "environmentVariable": credential["environment_variable"],
        }
    return {
        "provider_id": "hermes-ollama",
        "source_provider": route["provider"],
        "baseUrl": route["base_url"],
        "api": route["api"],
        "apiKey": api_key,
        "models": [{
            "id": route["model"],
            "contextWindow": compatibility["context_window"],
            "maxTokens": compatibility["max_output_tokens"],
            "supportsStreaming": compatibility["supports_streaming"],
            "supportsTools": compatibility["supports_tools"],
        }],
        "compat": {
            "supportsDeveloperRole": compatibility["supports_developer_role"],
            "supportsReasoningEffort": compatibility["supports_reasoning_effort"],
        },
        "authority": copy.deepcopy(route.get("authority")),
        "activeProfile": route.get("active_profile"),
        "runId": route.get("run_id"),
        "capturedAt": route.get("captured_at"),
    }


def resolve_model_route(policy: Mapping[str, Any], *, now: dt.datetime | None = None) -> RouteDecision:
    """Validate the public schema, enforce freshness/endpoints, and never select fallback."""

    if not isinstance(policy, Mapping):
        return _failure("policy must be an object")
    value = copy.deepcopy(dict(policy))
    errors = sorted(VALIDATOR.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "policy"
        return _failure(f"schema validation failed at {location}: {first.message}")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return _failure("resolver now must be timezone-aware")
    error = _semantic_error(value, current.astimezone(dt.timezone.utc))
    if error:
        return _failure(error)

    fallback_projection = None
    fallback = value.get("explicit_fallback")
    if fallback is not None:
        fallback_route = {**value, **fallback}
        fallback_route.pop("explicit_fallback", None)
        fallback_error = _semantic_error(fallback_route, current.astimezone(dt.timezone.utc))
        if fallback_error:
            return _failure(f"invalid explicit fallback: {fallback_error}")
        fallback_projection = _project(fallback_route)

    return RouteDecision(
        status="READY",
        reason="G-authority route projected without selecting explicit fallback",
        prime_config=_project(value),
        explicit_fallback=fallback_projection,
    )
