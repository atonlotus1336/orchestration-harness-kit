
"""Pinned, report-only Herdr reviewer lifecycle transport."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping

from .herdr_client import HerdrAmbiguousDelivery, HerdrClient, HerdrClientError
from .herdr_isolation import HerdrIsolationError, HerdrReviewIsolation
from .transport import BudgetViolation, ReviewBudgetTracker

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIGS = {
    "windows": ROOT / ".state/herdr-review/runtime-config.json",
    "wsl": ROOT / ".state/herdr-review/runtime-config-wsl.json",
}
LOCAL_LOCKS = {
    "windows": ROOT / ".state/herdr-review/runtime-lock.json",
    "wsl": ROOT / ".state/herdr-review/runtime-lock-wsl.json",
}
_SHA = re.compile(r"^[a-f0-9]{64}$")


def _load_effective_configuration(config_path: Path) -> tuple[Mapping[str, Any], Path]:
    selected = config_path
    target = "windows" if os.name == "nt" else "wsl"
    local_config = LOCAL_CONFIGS[target]
    local_lock = LOCAL_LOCKS[target]
    try:
        if (
            config_path.resolve()
            == (ROOT / "config/herdr-evaluator-adapters.json").resolve()
            and local_config.is_file()
        ):
            selected = local_config
        config = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise BudgetViolation("Herdr evaluator configuration unavailable") from exc
    lock_path = (
        local_lock
        if selected == local_config and local_lock.is_file()
        else ROOT / config.get("runtime_lock", "config/herdr-runtime-lock.json")
    )
    return config, lock_path
_RESULT_KEYS = frozenset({
    "schema_version", "receipt_id", "attempt_id", "reviewer_id",
    "packet_sha256", "source_identity", "findings", "finish_reason",
    "usage", "started_at", "finished_at", "herdr_refs",
})
_MUTATION_KEYS = frozenset({"patch", "diff", "replacement", "apply", "commands", "tools", "mounts"})


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _contains_mutation(value: Any) -> bool:
    if isinstance(value, Mapping):
        if set(value) & _MUTATION_KEYS:
            return True
        return any(_contains_mutation(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_mutation(child) for child in value)
    return False


def validate_herdr_result(value: Mapping[str, Any], expected: Mapping[str, str]) -> tuple[dict, ...]:
    keys = set(value)
    if keys != _RESULT_KEYS and keys != _RESULT_KEYS - {"herdr_refs"}:
        raise BudgetViolation("Herdr result is not a closed envelope")
    if value.get("schema_version") != "1.0":
        raise BudgetViolation("unsupported Herdr result schema")
    for key in ("receipt_id", "attempt_id", "reviewer_id", "packet_sha256", "source_identity"):
        if value.get(key) != expected.get(key):
            raise BudgetViolation("Herdr result lineage mismatch")
    if not _SHA.fullmatch(str(value.get("packet_sha256", ""))):
        raise BudgetViolation("invalid Herdr packet identity")
    findings = value.get("findings")
    if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
        raise BudgetViolation("Herdr findings must be objects")
    if _contains_mutation(findings):
        raise BudgetViolation("mutation payload is forbidden")
    if value.get("finish_reason") != "stop":
        raise BudgetViolation("Herdr reviewer did not reach a successful stop")
    usage = value.get("usage")
    if not isinstance(usage, Mapping) or set(usage) != {
        "prompt_tokens", "completion_tokens", "total_tokens"
    }:
        raise BudgetViolation("Herdr usage is incomplete")
    values = [usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
    if any(type(item) is not int or item < 0 for item in values) or values[0] + values[1] != values[2]:
        raise BudgetViolation("Herdr usage is inconsistent")
    for key in ("started_at", "finished_at"):
        try:
            datetime.fromisoformat(str(value.get(key)).replace("Z", "+00:00"))
        except ValueError as exc:
            raise BudgetViolation("Herdr result timestamp is invalid") from exc
    return tuple(findings)


@dataclass(frozen=True)
class HerdrReviewTransport:
    executable: Path
    runtime_lock: Path
    adapter_config: Path
    repository: Path
    launcher: Path
    reviewer_command: tuple[str, ...]
    receipt_id: str
    attempt_id: str
    reviewer_id: str
    s_level: str
    source_identity: str
    client_factory: Callable[..., HerdrClient] = field(default=HerdrClient, compare=False)
    isolation_factory: Callable[..., HerdrReviewIsolation] = field(default=HerdrReviewIsolation, compare=False)

    @classmethod
    def from_configuration(
        cls, config_path: Path, *, repository: Path, receipt_id: str,
        attempt_id: str, reviewer_id: str, s_level: str,
        source_identity: str, client_factory=HerdrClient,
        isolation_factory=HerdrReviewIsolation,
    ) -> "HerdrReviewTransport":
        config, lock_path = _load_effective_configuration(config_path)
        if config.get("schema_version") != "1.0" or config.get("mode") not in {
            "off", "observe", "supervised", "full",
        }:
            raise BudgetViolation("invalid Herdr evaluator mode")
        target = "windows" if os.name == "nt" else "wsl"
        target_config = config.get("targets", {}).get(target, {})
        reviewer = config.get("reviewers", {}).get(reviewer_id)
        if (config.get("mode") not in {"supervised", "full"}
                or target_config.get("qualified") is not True
                or not isinstance(target_config.get("executable"), str)
                or not isinstance(reviewer, Mapping)
                or set(reviewer) != {"command"}):
            raise BudgetViolation("Herdr reviewer is inactive or unqualified")
        command = reviewer["command"]
        if not isinstance(command, list) or not command:
            raise BudgetViolation("Herdr reviewer command is unavailable")
        return cls(
            executable=Path(target_config["executable"]),
            runtime_lock=lock_path,
            adapter_config=config_path,
            repository=repository,
            launcher=ROOT / config["fixed_launcher"],
            reviewer_command=tuple(command),
            receipt_id=receipt_id,
            attempt_id=attempt_id,
            reviewer_id=reviewer_id,
            s_level=s_level,
            source_identity=source_identity,
            client_factory=client_factory,
            isolation_factory=isolation_factory,
        )

    def _configuration(self) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        try:
            lock = json.loads(self.runtime_lock.read_text(encoding="utf-8"))
            config, _lock_path = _load_effective_configuration(self.adapter_config)
        except (OSError, UnicodeError, ValueError) as exc:
            raise BudgetViolation("Herdr evaluator configuration unavailable") from exc
        if lock.get("schema_version") != "1.0" or config.get("schema_version") != "1.0":
            raise BudgetViolation("unsupported Herdr evaluator configuration")
        return lock, config

    def validate(self) -> None:
        lock, config = self._configuration()
        if config.get("mode") not in {"supervised", "full"}:
            raise BudgetViolation("Herdr review transport is inactive")
        if lock.get("runnable") is not True or not isinstance(lock.get("schema_sha256"), str):
            raise BudgetViolation("Herdr runtime is not qualified")
        if self.s_level not in {"S1", "S2", "S3", "S4"}:
            raise BudgetViolation("invalid Herdr review level")
        if not self.executable.is_file() or not self.launcher.is_file():
            raise BudgetViolation("Herdr executable or fixed launcher unavailable")
        if not self.reviewer_command or any(not isinstance(item, str) or not item for item in self.reviewer_command):
            raise BudgetViolation("Herdr reviewer command is not pinned")

    def payload(self, skill: str, contents: Mapping[str, str], output_cap: int) -> bytes:
        output_cap = min(output_cap, 2048)
        source_hashes = {
            path: hashlib.sha256(body.encode()).hexdigest()
            for path, body in sorted(contents.items())
        }
        value = {
            "schema_version": "1.0",
            "receipt_id": self.receipt_id,
            "attempt_id": self.attempt_id,
            "reviewer_id": self.reviewer_id,
            "s_level": self.s_level,
            "packet_sha256": "0" * 64,
            "source_identity": self.source_identity,
            "source_hashes": source_hashes,
            "rubric": skill,
            "content": dict(sorted(contents.items())),
            "deadline_ms": 300_000,
            "reserved_budget": {
                "max_context_bytes": sum(len(body.encode()) for body in contents.values()) + len(skill.encode()),
                "max_model_calls": 1,
                "max_output_tokens": output_cap,
                "max_total_tokens": output_cap + sum(len(body.encode()) for body in contents.values()),
                "max_elapsed_seconds": 300,
            },
            "max_tokens": output_cap,
        }
        identity = hashlib.sha256(_canonical({key: item for key, item in value.items() if key != "packet_sha256"})).hexdigest()
        value["packet_sha256"] = identity
        return _canonical(value)

    def _admitted_cap(self, skill: str, contents: Mapping[str, str],
                      budget: ReviewBudgetTracker) -> int:
        if budget.admitted_packets is None:
            return budget.limits["max_output_tokens"]
        candidates = []
        for packet in budget.admitted_packets:
            value = json.loads(packet)
            if (value.get("reviewer_id") == self.reviewer_id
                    and value.get("receipt_id") == self.receipt_id
                    and value.get("content") == dict(contents)):
                candidates.append(value)
        if len(candidates) != 1:
            raise BudgetViolation("Herdr admitted packet identity is not unique")
        return candidates[0]["max_tokens"]

    def run(self, skill: str, contents: Mapping[str, str],
            budget: ReviewBudgetTracker) -> tuple[dict, ...]:
        self.validate()
        cap = self._admitted_cap(skill, contents, budget)
        packet = self.payload(skill, contents, cap)
        budget.reserve(packet)
        request = json.loads(packet)
        expected = {
            key: request[key] for key in (
                "receipt_id", "attempt_id", "reviewer_id",
                "packet_sha256", "source_identity",
            )
        }
        isolation = self.isolation_factory(
            self.repository, self.receipt_id, self.attempt_id, self.reviewer_id
        ).create()
        session_name = "eval-" + hashlib.sha256(
            f"{self.receipt_id}:{self.attempt_id}:{self.reviewer_id}".encode()
        ).hexdigest()[:20]
        client = self.client_factory(self.executable, session=session_name)
        workspace_id = None
        try:
            isolation.write_request(request)
            start_server = getattr(client, "start_server", None)
            if callable(start_server):
                start_server(cwd=isolation.root, env=isolation.environment())
            ref = client.create_workspace(
                cwd=isolation.root,
                label=session_name,
                env=isolation.environment(),
            )
            workspace_id = ref.workspace_id
            argv = (
                sys.executable, str(self.launcher),
                "--request", str(isolation.request_path),
                "--result", str(isolation.result_path), "--", *self.reviewer_command,
            )
            client.run_fixed_launcher(ref.pane_id, argv)
            # The fixed wrapper is a pane process, not a Herdr-managed agent.
            # Completion is established only by the closed result file; terminal
            # output and agent event ordering are deliberately irrelevant.
            result = isolation.wait_result(timeout_ms=request["deadline_ms"])
            findings = validate_herdr_result(result, expected)
            budget.settle(result["usage"])
            budget.last_finish_reason = result["finish_reason"]
            client.close_workspace(workspace_id)
            close_session = getattr(client, "close_session", None)
            if callable(close_session):
                close_session()
            isolation.cleanup()
            return findings
        except (HerdrAmbiguousDelivery, HerdrClientError, HerdrIsolationError, BudgetViolation, OSError, ValueError) as exc:
            close_session = getattr(client, "close_session", None)
            if callable(close_session):
                try:
                    close_session()
                except Exception:
                    pass
            isolation.quarantine(type(exc).__name__)
            budget.failed = True
            raise BudgetViolation("Herdr review held for explicit reconciliation") from exc


__all__ = [
    "ConfiguredHerdrReviewStarter", "HerdrReviewTransport",
    "validate_herdr_result", "HerdrReviewCapabilityService",
]


@dataclass(frozen=True)
class ConfiguredHerdrReviewStarter:
    """Governed admission point for the already-configured evaluator transport."""

    config_path: Path = ROOT / "config/herdr-evaluator-adapters.json"

    def __call__(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            config, lock_path = _load_effective_configuration(self.config_path)
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, KeyError) as exc:
            raise BudgetViolation("Herdr reviewer activation state is unavailable") from exc
        reviewer = config.get("reviewers", {}).get(arguments.get("reviewer_id"))
        if (
            config.get("mode") not in {"supervised", "full"}
            or lock.get("runnable") is not True
            or lock.get("activation_state") not in {"supervised", "full"}
            or not isinstance(reviewer, Mapping)
            or set(reviewer) != {"command"}
            or not isinstance(reviewer.get("command"), list)
            or not reviewer["command"]
        ):
            raise BudgetViolation("Herdr reviewer is not activated")
        return {
            "status": "START_AUTHORIZED",
            "receipt_id": arguments["receipt_id"],
            "attempt_id": arguments["attempt_id"],
            "reviewer_id": arguments["reviewer_id"],
            "request_sha256": arguments["request_sha256"],
            "s_level": arguments["s_level"],
            "transport": "herdr",
        }

class HerdrReviewCapabilityService:
    """Receipt-bound capability adapter. The default MCP instance has no starter."""

    def __init__(self, *, switch_path: Path | None = None, starter=None):
        self.switch_path = switch_path
        self.starter = starter

    async def call(self, tool: str, arguments: Mapping[str, Any],
                   context: Mapping[str, Any]) -> Mapping[str, Any]:
        from prime_bridge.orchestration_switch import operation_permitted
        if tool != "start" or set(arguments) != {
            "harness", "receipt_id", "attempt_id", "reviewer_id",
            "request_sha256", "s_level", "s4_confirmed",
        }:
            raise BudgetViolation("invalid Herdr reviewer capability request")
        harness = arguments.get("harness")
        if not operation_permitted(
            "herdr-review", harness, "start", self.switch_path
        ):
            raise BudgetViolation("Herdr reviewer capability is off")
        capability_receipt = context.get("capability_receipt")
        if (context.get("harness") != harness
                or not isinstance(context.get("request_id"), str)
                or not context.get("request_id")
                or not isinstance(capability_receipt, str)
                or _SHA.fullmatch(capability_receipt) is None):
            raise BudgetViolation("Herdr reviewer approval binding mismatch")
        if arguments.get("s_level") == "S4" and arguments.get("s4_confirmed") is not True:
            raise BudgetViolation("S4 confirmation required")
        if self.starter is None:
            raise BudgetViolation("Herdr reviewer starter is not connected")
        result = self.starter(arguments)
        if hasattr(result, "__await__"):
            result = await result
        return result
