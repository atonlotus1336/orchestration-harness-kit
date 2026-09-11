"""Fail-closed report-only boundary for reviewer providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Callable, Mapping

from .models import Verdict
from .transport import JsonReviewTransport, ReviewBudgetTracker


class CapabilityViolation(RuntimeError):
    pass


def _safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\\" in value or PureWindowsPath(value).drive or path.is_absolute() or ".." in path.parts:
        raise CapabilityViolation(f"unsafe path: {value!r}")
    return str(path)


@dataclass(frozen=True)
class ReviewerCapability:
    readable_files: tuple[str, ...]
    allowed_commands: tuple[str, ...] = ()
    allowed_tests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized = tuple(sorted({_safe_path(item) for item in self.readable_files}))
        object.__setattr__(self, "readable_files", normalized)


@dataclass
class ReportOnlyContext:
    capability: ReviewerCapability
    contents: Mapping[str, str]
    findings: list[dict] = field(default_factory=list)

    def read(self, path: str) -> str:
        normalized = _safe_path(path)
        if normalized not in self.capability.readable_files:
            raise CapabilityViolation(f"out-of-scope read denied: {normalized}")
        if normalized not in self.contents:
            raise CapabilityViolation(f"declared content unavailable: {normalized}")
        return self.contents[normalized]

    def emit(self, finding: dict) -> None:
        if any(key in finding for key in ("patch", "diff", "replacement", "apply")):
            raise CapabilityViolation("patch proposal/application denied")
        self.findings.append(finding)

    def write(self, *_: object) -> None:
        raise CapabilityViolation("source write denied")

    def apply_patch(self, *_: object) -> None:
        raise CapabilityViolation("patch proposal/application denied")

    def network(self, *_: object) -> None:
        raise CapabilityViolation("network denied")

    def run_command(self, command: str) -> None:
        if command not in self.capability.allowed_commands:
            raise CapabilityViolation(f"unallowlisted command denied: {command}")

    def run_test(self, command: str) -> None:
        if command not in self.capability.allowed_tests:
            raise CapabilityViolation(f"unallowlisted test denied: {command}")

    def follow_on(self, *_: object) -> None:
        raise CapabilityViolation("follow-on execution denied")


@dataclass(frozen=True)
class ReviewOutcome:
    verdict: Verdict
    findings: tuple[dict, ...] = ()
    violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderAttestation:
    """Capabilities of the transport hosting reviewer prompt text."""

    tool_names: tuple[str, ...] = ()
    source_mounted: bool = False
    patch_channel: bool = False
    arbitrary_command: bool = False

    @property
    def report_only(self) -> bool:
        return not (
            self.tool_names
            or self.source_mounted
            or self.patch_channel
            or self.arbitrary_command
        )


@dataclass(frozen=True)
class ReportOnlyProvider:
    """Owned transport callback plus a no-tools/no-source attestation."""

    invoke: Callable[[ReportOnlyContext], object] | None = None
    attestation: ProviderAttestation = ProviderAttestation()
    transport: JsonReviewTransport | None = None


class ReviewerAdapter:
    """Run a provider with content only and prove reviewed source stayed unchanged."""

    def __init__(
        self,
        capability: ReviewerCapability,
        *,
        contents: Mapping[str, str],
        source_hashes: Mapping[str, str],
        hash_provider: Callable[[tuple[str, ...]], Mapping[str, str]],
    ) -> None:
        self.capability = capability
        self.contents = dict(contents)
        self.source_hashes = dict(source_hashes)
        self.hash_provider = hash_provider

    def run(self, provider: ReportOnlyProvider, *, budget: ReviewBudgetTracker | None = None,
            skill: str = "") -> ReviewOutcome:
        if not self.capability.readable_files:
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=("empty scope",))
        if set(self.source_hashes) != set(self.capability.readable_files):
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=("incomplete pre-review hashes",))
        if not provider.attestation.report_only:
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=("provider is not report-only",))
        if type(provider.transport) is not JsonReviewTransport or provider.invoke is not None or budget is None:
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=("unmetered reviewer callback is forbidden",))
        import hashlib
        expected = {p: hashlib.sha256(self.contents[p].encode()).hexdigest() for p in self.capability.readable_files if p in self.contents}
        try:
            if expected != self.source_hashes or dict(self.hash_provider(self.capability.readable_files)) != expected:
                return ReviewOutcome(Verdict.INCONCLUSIVE, violations=("source content or pre-review hash mismatch",))
            findings = provider.transport.run(skill, self.contents, budget)
            if dict(self.hash_provider(self.capability.readable_files)) != expected:
                raise CapabilityViolation("source hash mismatch after review")
        except Exception as exc:
            budget.failed = True
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=(f"review refused: {type(exc).__name__}",))
        return ReviewOutcome(Verdict.FINDINGS if findings else Verdict.PASS, findings=findings)
