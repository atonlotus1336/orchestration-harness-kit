"""Closed, hashable task packets for the Prime-to-OMP boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


class OmpTaskPacketError(ValueError):
    """The proposed packet escaped the closed task contract."""


SCHEMA_VERSION = "1.0"
EDIT_CLASSES = frozenset({
    "create", "insert", "exact_replace", "counted_replace", "refactor",
    "test_repair", "multi_file", "documentation", "configuration",
})
RISK_TIERS = frozenset({"low", "moderate", "high"})
PACKET_FIELDS = frozenset({
    "schema_version", "objective", "edit_class", "allowed_paths",
    "required_observations", "invariants", "risk_tier", "size_budget",
    "verification_intent", "dependencies",
})
OPTIONAL_PACKET_FIELDS = frozenset({"semantic_assertions", "change_contract"})
SIZE_FIELDS = frozenset({"max_files", "max_changed_bytes", "max_complexity"})
FORBIDDEN_PARTS = frozenset({".git", "node_modules", ".env", "secrets", "credentials"})
_HASH = re.compile(r"^[0-9a-f]{64}$")
_COMMAND_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _bounded_strings(value: Any, label: str, *, maximum: int = 16) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise OmpTaskPacketError(f"{label} must be a bounded array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 512:
            raise OmpTaskPacketError(f"{label} contains an invalid value")
        normalized = item.strip()
        if normalized in result:
            raise OmpTaskPacketError(f"{label} contains duplicates")
        result.append(normalized)
    return tuple(result)


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise OmpTaskPacketError("allowed path is invalid")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
        raise OmpTaskPacketError("allowed path must be project-relative")
    if any(part.lower() in FORBIDDEN_PARTS for part in candidate.parts):
        raise OmpTaskPacketError("allowed path is generated or sensitive")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise OmpTaskPacketError("allowed path is invalid")
    return normalized


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise OmpTaskPacketError(f"{label} must be null or a bounded non-empty string")
    return value.strip()


@dataclass(frozen=True)
class RegressionTestEvidence:
    """Deterministic before/after evidence for a reproducible bug fix."""

    pre_fix_command: str
    pre_fix_exit_status: int
    post_fix_command: str
    post_fix_exit_status: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RegressionTestEvidence":
        fields = {
            "pre_fix_command", "pre_fix_exit_status",
            "post_fix_command", "post_fix_exit_status",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise OmpTaskPacketError("regression test evidence fields do not match the closed schema")
        pre_command = _optional_string(value["pre_fix_command"], "pre-fix command")
        post_command = _optional_string(value["post_fix_command"], "post-fix command")
        if pre_command is None or post_command is None:
            raise OmpTaskPacketError("regression test evidence commands are required")
        pre_status = value["pre_fix_exit_status"]
        post_status = value["post_fix_exit_status"]
        if type(pre_status) is not int or type(post_status) is not int:
            raise OmpTaskPacketError("regression test exit statuses must be integers")
        return cls(pre_command, pre_status, post_command, post_status)

    @property
    def proves_regression(self) -> bool:
        return (
            self.pre_fix_command == self.post_fix_command
            and self.pre_fix_exit_status != 0
            and self.post_fix_exit_status == 0
        )

    def canonical_data(self) -> dict[str, Any]:
        return {
            "post_fix_command": self.post_fix_command,
            "post_fix_exit_status": self.post_fix_exit_status,
            "pre_fix_command": self.pre_fix_command,
            "pre_fix_exit_status": self.pre_fix_exit_status,
        }


@dataclass(frozen=True)
class ChangeContract:
    """Shared coding-only guardrails carried inside a closed OMP task packet."""

    objective: str
    declared_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    semantic_assertions: tuple[str, ...]
    bugfix_intent: bool
    regression_test_expected: bool
    regression_test_evidence: RegressionTestEvidence | None
    regression_test_exemption: str | None
    alternative_verification: tuple[str, ...]
    cleanup_boundary_enabled: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ChangeContract":
        fields = {
            "objective", "declared_paths", "acceptance_criteria", "semantic_assertions",
            "bugfix_intent", "regression_test_expected", "regression_test_evidence",
            "regression_test_exemption", "alternative_verification",
            "cleanup_boundary_enabled",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise OmpTaskPacketError("change contract fields do not match the closed schema")
        objective = value["objective"]
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 4096:
            raise OmpTaskPacketError("change contract objective must be between 1 and 4096 characters")
        for flag in ("bugfix_intent", "regression_test_expected", "cleanup_boundary_enabled"):
            if type(value[flag]) is not bool:
                raise OmpTaskPacketError(f"change contract {flag} must be boolean")
        evidence_raw = value["regression_test_evidence"]
        evidence = None if evidence_raw is None else RegressionTestEvidence.from_mapping(evidence_raw)
        paths_raw = value["declared_paths"]
        if not isinstance(paths_raw, (list, tuple)) or not 1 <= len(paths_raw) <= 8:
            raise OmpTaskPacketError("change contract declared paths must contain between 1 and 8 paths")
        paths = tuple(_path(item) for item in paths_raw)
        if len(paths) != len(set(paths)):
            raise OmpTaskPacketError("change contract declared paths contain duplicates")
        return cls(
            objective=objective.strip(),
            declared_paths=paths,
            acceptance_criteria=_bounded_strings(value["acceptance_criteria"], "acceptance criteria"),
            semantic_assertions=_bounded_strings(value["semantic_assertions"], "change contract semantic assertions"),
            bugfix_intent=value["bugfix_intent"],
            regression_test_expected=value["regression_test_expected"],
            regression_test_evidence=evidence,
            regression_test_exemption=_optional_string(value["regression_test_exemption"], "regression test exemption"),
            alternative_verification=_bounded_strings(value["alternative_verification"], "alternative verification"),
            cleanup_boundary_enabled=value["cleanup_boundary_enabled"],
        )

    def canonical_data(self) -> dict[str, Any]:
        return {
            "acceptance_criteria": list(self.acceptance_criteria),
            "alternative_verification": list(self.alternative_verification),
            "bugfix_intent": self.bugfix_intent,
            "cleanup_boundary_enabled": self.cleanup_boundary_enabled,
            "declared_paths": list(self.declared_paths),
            "objective": self.objective,
            "regression_test_evidence": (
                self.regression_test_evidence.canonical_data()
                if self.regression_test_evidence is not None else None
            ),
            "regression_test_exemption": self.regression_test_exemption,
            "regression_test_expected": self.regression_test_expected,
            "semantic_assertions": list(self.semantic_assertions),
        }


_BUGFIX_INTENT = re.compile(
    r"\b(?:bug|defect|regression|fix(?:e[ds]|ing)?|repair(?:ed|ing)?|broken|failing|failure)\b",
    re.IGNORECASE,
)


def build_change_contract(
    *, objective: str, declared_paths: tuple[str, ...],
    semantic_assertions: tuple[str, ...] = (),
    acceptance_criteria: tuple[str, ...] = (),
    bugfix_intent: bool | None = None,
    regression_test_evidence: RegressionTestEvidence | None = None,
    regression_test_exemption: str | None = None,
    alternative_verification: tuple[str, ...] = (),
    cleanup_boundary_enabled: bool = True,
) -> ChangeContract:
    """Create the canonical contract only when a coding packet is compiled."""
    inferred_bugfix = bool(_BUGFIX_INTENT.search(objective)) if bugfix_intent is None else bugfix_intent
    criteria = acceptance_criteria or semantic_assertions or ("Complete the declared objective.",)
    return ChangeContract.from_mapping({
        "objective": objective,
        "declared_paths": list(declared_paths),
        "acceptance_criteria": list(criteria),
        "semantic_assertions": list(semantic_assertions),
        "bugfix_intent": inferred_bugfix,
        "regression_test_expected": inferred_bugfix,
        "regression_test_evidence": (
            regression_test_evidence.canonical_data()
            if regression_test_evidence is not None else None
        ),
        "regression_test_exemption": regression_test_exemption,
        "alternative_verification": list(alternative_verification),
        "cleanup_boundary_enabled": cleanup_boundary_enabled,
    })


@dataclass(frozen=True)
class OmpTaskPacket:
    objective: str
    edit_class: str
    allowed_paths: tuple[str, ...]
    required_observations: tuple[str, ...]
    invariants: tuple[str, ...]
    risk_tier: str
    size_budget: Mapping[str, int]
    verification_intent: tuple[str, ...]
    semantic_assertions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    change_contract: ChangeContract | None = None
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OmpTaskPacket":
        if (
            not isinstance(value, Mapping)
            or not PACKET_FIELDS.issubset(value)
            or set(value) - PACKET_FIELDS - OPTIONAL_PACKET_FIELDS
        ):
            raise OmpTaskPacketError("task packet fields do not match the closed schema")
        objective = value["objective"]
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 4096:
            raise OmpTaskPacketError("objective must be between 1 and 4096 characters")
        edit_class = value["edit_class"]
        risk_tier = value["risk_tier"]
        if edit_class not in EDIT_CLASSES:
            raise OmpTaskPacketError("edit class is not supported")
        if risk_tier not in RISK_TIERS:
            raise OmpTaskPacketError("risk tier is not supported")
        paths_raw = value["allowed_paths"]
        if not isinstance(paths_raw, (list, tuple)) or not 1 <= len(paths_raw) <= 8:
            raise OmpTaskPacketError("allowed paths must contain between 1 and 8 paths")
        paths = tuple(_path(item) for item in paths_raw)
        if len(paths) != len(set(paths)):
            raise OmpTaskPacketError("allowed paths contain duplicates")
        budget = value["size_budget"]
        if not isinstance(budget, Mapping) or set(budget) != SIZE_FIELDS:
            raise OmpTaskPacketError("size budget fields do not match the closed schema")
        normalized_budget: dict[str, int] = {}
        for key in sorted(SIZE_FIELDS):
            item = budget[key]
            if type(item) is not int or item <= 0:
                raise OmpTaskPacketError("size budget values must be positive integers")
            normalized_budget[key] = item
        if normalized_budget["max_files"] > 8 or len(paths) > normalized_budget["max_files"]:
            raise OmpTaskPacketError("allowed paths exceed the file budget")
        dependencies = _bounded_strings(value["dependencies"], "dependencies", maximum=16)
        if any(not _HASH.fullmatch(item) for item in dependencies):
            raise OmpTaskPacketError("dependencies must be canonical packet hashes")
        if value["schema_version"] != SCHEMA_VERSION:
            raise OmpTaskPacketError("task packet schema version is unsupported")
        semantic_assertions = _bounded_strings(
            value.get("semantic_assertions", ()), "semantic assertions",
        )
        if "change_contract" in value and value["change_contract"] is None:
            raise OmpTaskPacketError("change contract must be an object when present")
        change_contract = (
            ChangeContract.from_mapping(value["change_contract"])
            if "change_contract" in value else None
        )
        if change_contract is not None and (
            change_contract.objective != objective.strip()
            or change_contract.declared_paths != paths
            or change_contract.semantic_assertions != semantic_assertions
        ):
            raise OmpTaskPacketError("change contract must match packet objective, paths, and assertions")
        return cls(
            objective=objective.strip(), edit_class=edit_class, allowed_paths=paths,
            required_observations=_bounded_strings(value["required_observations"], "required observations"),
            invariants=_bounded_strings(value["invariants"], "invariants"),
            risk_tier=risk_tier, size_budget=normalized_budget,
            verification_intent=_bounded_strings(value["verification_intent"], "verification intent"),
            semantic_assertions=semantic_assertions,
            dependencies=dependencies, change_contract=change_contract,
        )

    def canonical_data(self) -> dict[str, Any]:
        value = {
            "allowed_paths": list(self.allowed_paths),
            "dependencies": list(self.dependencies),
            "edit_class": self.edit_class,
            "invariants": list(self.invariants),
            "objective": self.objective,
            "required_observations": list(self.required_observations),
            "risk_tier": self.risk_tier,
            "schema_version": self.schema_version,
            "semantic_assertions": list(self.semantic_assertions),
            "size_budget": dict(sorted(self.size_budget.items())),
            "verification_intent": list(self.verification_intent),
        }
        if self.change_contract is not None:
            value["change_contract"] = self.change_contract.canonical_data()
        return value

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_data(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def packet_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def verification_command_id(packet: OmpTaskPacket) -> str:
    """Resolve one hash-bound command intent, retaining static verification by default."""
    commands = [
        item.removeprefix("command:")
        for item in packet.verification_intent
        if item.startswith("command:")
    ]
    if len(commands) > 1:
        raise OmpTaskPacketError("verification command intent must contain at most one command")
    if not commands:
        return "packet_static"
    if not _COMMAND_ID.fullmatch(commands[0]):
        raise OmpTaskPacketError("verification command intent is invalid")
    return commands[0]


def compile_atomic_task_packet(
    *, objective: str, path: str, edit_class: str = "exact_replace",
    verification_intent: tuple[str, ...] = (), risk_tier: str = "low",
    semantic_assertions: tuple[str, ...] = (),
    acceptance_criteria: tuple[str, ...] = (),
    bugfix_intent: bool | None = None,
    regression_test_evidence: RegressionTestEvidence | None = None,
    regression_test_exemption: str | None = None,
    alternative_verification: tuple[str, ...] = (),
    cleanup_boundary_enabled: bool = True,
) -> OmpTaskPacket:
    """Compile an explicit one-file request without an exploratory model turn."""
    contract = build_change_contract(
        objective=objective, declared_paths=(path,),
        semantic_assertions=semantic_assertions,
        acceptance_criteria=acceptance_criteria,
        bugfix_intent=bugfix_intent,
        regression_test_evidence=regression_test_evidence,
        regression_test_exemption=regression_test_exemption,
        alternative_verification=alternative_verification,
        cleanup_boundary_enabled=cleanup_boundary_enabled,
    )
    return OmpTaskPacket.from_mapping({
        "schema_version": SCHEMA_VERSION,
        "objective": objective,
        "edit_class": edit_class,
        "allowed_paths": [path],
        "required_observations": [f"read:{path}"],
        "invariants": ["remain within the declared path", "preserve unrelated behavior"],
        "risk_tier": risk_tier,
        "size_budget": {"max_files": 1, "max_changed_bytes": 65536, "max_complexity": 4},
        "verification_intent": list(verification_intent),
        "semantic_assertions": list(semantic_assertions),
        "change_contract": contract.canonical_data(),
        "dependencies": [],
    })


def project_packet_depth(packet: OmpTaskPacket, limits: Mapping[str, Any]) -> OmpTaskPacket:
    """Apply smaller verified bounds without changing packet scope or safety intent."""
    required = {"max_files", "max_changed_bytes", "max_complexity"}
    if not isinstance(limits, Mapping) or not required.issubset(limits):
        raise OmpTaskPacketError("depth limits are incomplete")
    maximum_files = limits["max_files"]
    if type(maximum_files) is not int or maximum_files < len(packet.allowed_paths):
        raise OmpTaskPacketError("depth profile cannot contain the declared paths")
    value = packet.canonical_data()
    value["size_budget"] = {
        "max_files": min(packet.size_budget["max_files"], maximum_files),
        "max_changed_bytes": min(packet.size_budget["max_changed_bytes"], limits["max_changed_bytes"]),
        "max_complexity": min(packet.size_budget["max_complexity"], limits["max_complexity"]),
    }
    return OmpTaskPacket.from_mapping(value)
