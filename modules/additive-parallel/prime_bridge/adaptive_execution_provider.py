"""Deterministic provider selection beneath the T0-T4 coding router."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .orchestration_switch import effective_mode


ELIGIBLE_ORCA_LANES = frozenset({"T3_PRIME_OMP", "T4_AUTONOMOUS"})
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
QUALIFICATION_SCHEMA_VERSION = "1.1"


class AdaptiveProviderSelectionError(ValueError):
    pass


def default_qualification_path() -> Path:
    override = os.environ.get("LAPART_ORCA_CODING_QUALIFICATION")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / ".state" / "orca-coding" / "qualification.json"


def qualification_contract_sha256() -> str:
    """Fingerprint only code that can change Orca qualification semantics."""
    root = Path(__file__).resolve().parents[1]
    paths = (
        Path(__file__).resolve(),
        root / "prime_bridge" / "orca_execution.py",
        root / "prime_bridge" / "worker_transport.py",
        root / "scripts" / "qualify-orca-coding.py",
    )
    material = {
        str(path.relative_to(root)).replace("\\", "/"):
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    return hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


@dataclass(frozen=True)
class AdaptiveProviderDecision:
    schema_version: str
    provider: str
    status: str
    reason: str
    actual_lane: str
    harness: str
    admitted_count: int
    qualification_reference: str | None
    automatic_dispatch_authorized: bool
    max_parallel: int
    decided_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _native(*, reason: str, actual_lane: str, harness: str,
            admitted_count: int, now: datetime) -> AdaptiveProviderDecision:
    return AdaptiveProviderDecision(
        "1.0", "native", "BYPASSED", reason, actual_lane, harness,
        admitted_count, None, False, 0,
        now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def _qualification(path: Path, *, harness: str, route_sha256: str,
                   admitted_count: int, authority_key: bytes | None,
                   now: datetime) -> tuple[bool, str | None, str, Mapping[str, Any] | None]:
    if not path.is_file() or path.is_symlink():
        return False, None, "ORCA_AB_QUALIFICATION_MISSING", None
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, None, "ORCA_AB_QUALIFICATION_INVALID", None
    required = {"schema_version", "provider", "records", "authority_hmac_sha256"}
    if (not isinstance(value, dict) or set(value) != required
            or value.get("schema_version") != QUALIFICATION_SCHEMA_VERSION
            or value.get("provider") != "orca"
            or not isinstance(value.get("records"), dict)):
        return False, None, "ORCA_AB_QUALIFICATION_INVALID", None
    unsigned = {key: item for key, item in value.items() if key != "authority_hmac_sha256"}
    expected = hmac.new(
        authority_key or b"",
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    if (not authority_key or len(authority_key) < 16
            or not hmac.compare_digest(str(value["authority_hmac_sha256"]), expected)):
        return False, None, "ORCA_AB_QUALIFICATION_UNSIGNED", None
    record = value["records"].get(harness)
    fields = {
        "status", "harness", "route_sha256", "qualified_at", "validity",
        "sample_count", "quality_regressions", "wall_time_ratio",
        "native_successes", "orca_successes", "evidence_sha256",
        "automatic_dispatch_authorized", "max_parallel", "adapter_launch",
        "qualification_contract_sha256", "adapter_sha256",
        "orca_executable_sha256",
    }
    if (not isinstance(record, dict) or set(record) != fields
            or record.get("status") != "qualified"
            or record.get("harness") != harness
            or record.get("route_sha256") != route_sha256
            or SHA256.fullmatch(str(record.get("evidence_sha256") or "")) is None
            or type(record.get("sample_count")) is not int
            or record["sample_count"] < 2
            or type(record.get("quality_regressions")) is not int
            or record["quality_regressions"] != 0
            or type(record.get("native_successes")) is not int
            or type(record.get("orca_successes")) is not int
            or min(record["native_successes"], record["orca_successes"]) < 1
            or not isinstance(record.get("wall_time_ratio"), (int, float))
            or record["wall_time_ratio"] <= 0
            or record.get("automatic_dispatch_authorized") is not True
            or record.get("validity") != "until_material_change"
            or record.get("qualification_contract_sha256")
            != qualification_contract_sha256()
            or SHA256.fullmatch(str(record.get("adapter_sha256") or "")) is None
            or SHA256.fullmatch(
                str(record.get("orca_executable_sha256") or "")
            ) is None
            or record.get("adapter_launch") not in {"native", "terminal"}
            or type(record.get("max_parallel")) is not int
            or record["max_parallel"] < admitted_count):
        return False, None, "ORCA_AB_QUALIFICATION_INVALID", None
    try:
        qualified = datetime.fromisoformat(record["qualified_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False, None, "ORCA_AB_QUALIFICATION_INVALID", None
    if qualified.tzinfo is None or qualified > now:
        return False, None, "ORCA_AB_QUALIFICATION_INVALID", None
    reference = hashlib.sha256(raw).hexdigest()
    return True, reference, "ORCA_AB_QUALIFIED", record


def select_execution_provider(
    *, catalog, provider: Any | None, actual_lane: str, harness: str,
    admitted_count: int, route_sha256: str, switch_path=None,
    qualification_path: Path | str | None = None,
    authority_key: bytes | None = None,
    now: datetime | None = None,
) -> AdaptiveProviderDecision:
    """Select Orca only when every installed authority independently agrees."""
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if actual_lane not in ELIGIBLE_ORCA_LANES:
        return _native(reason="LANE_NOT_ORCA_ELIGIBLE", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    if type(admitted_count) is not int or admitted_count < 2:
        return _native(reason="SERIAL_ADMISSION", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    spec = getattr(catalog, "specs", {}).get("orca")
    if spec is None or "router" not in spec.consumers:
        return _native(reason="ORCA_ROUTER_PROVIDER_UNREGISTERED", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    try:
        if effective_mode("orca", harness, switch_path) != "full":
            return _native(reason="ORCA_NOT_FULL", actual_lane=actual_lane,
                           harness=harness, admitted_count=admitted_count, now=clock)
    except Exception:
        return _native(reason="ORCA_SWITCH_INVALID", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    if SHA256.fullmatch(route_sha256) is None:
        raise AdaptiveProviderSelectionError("route fingerprint is invalid")
    ready, reference, reason, qualification = _qualification(
        Path(qualification_path or default_qualification_path()),
        harness=harness, route_sha256=route_sha256,
        admitted_count=admitted_count, authority_key=authority_key, now=clock,
    )
    if not ready:
        return _native(reason=reason, actual_lane=actual_lane, harness=harness,
                       admitted_count=admitted_count, now=clock)
    if provider is None:
        return _native(reason="ORCA_PROVIDER_UNAVAILABLE", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    try:
        status: Mapping[str, Any] = provider.status((harness,))
        row = status.get("harnesses", {}).get(harness, {})
    except Exception:
        return _native(reason="ORCA_STATUS_UNAVAILABLE", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    if row.get("runtime_ready") != "READY" or row.get("qualification") != "production":
        return _native(reason="ORCA_RUNTIME_NOT_PRODUCTION_READY", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    if row.get("launch") != qualification["adapter_launch"]:
        return _native(reason="ORCA_ADAPTER_QUALIFICATION_MISMATCH", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    try:
        material = provider.qualification_fingerprint(harness)
    except Exception:
        return _native(reason="ORCA_MATERIAL_FINGERPRINT_UNAVAILABLE",
                       actual_lane=actual_lane, harness=harness,
                       admitted_count=admitted_count, now=clock)
    if (material.get("adapter_sha256") != qualification["adapter_sha256"]
            or material.get("orca_executable_sha256")
            != qualification["orca_executable_sha256"]):
        return _native(reason="ORCA_MATERIAL_FINGERPRINT_MISMATCH",
                       actual_lane=actual_lane, harness=harness,
                       admitted_count=admitted_count, now=clock)
    try:
        probe = provider.probe(harness)
    except Exception:
        return _native(reason="ORCA_RUNTIME_PROBE_FAILED", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    if probe.get("status") != "READY":
        return _native(reason="ORCA_RUNTIME_NOT_REACHABLE", actual_lane=actual_lane,
                       harness=harness, admitted_count=admitted_count, now=clock)
    return AdaptiveProviderDecision(
        "1.0", "orca", "SELECTED", "ORCA_ADAPTIVE_PRODUCTION_SELECTED",
        actual_lane, harness, admitted_count, reference, True,
        int(qualification["max_parallel"]),
        clock.isoformat().replace("+00:00", "Z"),
    )


__all__ = [
    "AdaptiveProviderDecision", "AdaptiveProviderSelectionError",
    "ELIGIBLE_ORCA_LANES", "default_qualification_path",
    "qualification_contract_sha256",
    "select_execution_provider",
]
