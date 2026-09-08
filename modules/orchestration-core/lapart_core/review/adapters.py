"""Fail-closed report-only boundary for reviewer providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Mapping

from .models import Verdict


class CapabilityViolation(RuntimeError):
    pass


def _safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
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

    invoke: Callable[[ReportOnlyContext], object]
    attestation: ProviderAttestation = ProviderAttestation()


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

    def run(self, provider: ReportOnlyProvider) -> ReviewOutcome:
        if not self.capability.readable_files:
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=("empty scope",))
        if set(self.source_hashes) != set(self.capability.readable_files):
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=("incomplete pre-review hashes",))
        if not provider.attestation.report_only:
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=("provider is not report-only",))
        context = ReportOnlyContext(self.capability, self.contents)
        violations: list[str] = []
        try:
            returned = provider.invoke(context)
            if returned not in (None, context.findings):
                raise CapabilityViolation("provider returned an unsupported payload")
        except CapabilityViolation as exc:
            violations.append(str(exc))
        except Exception as exc:
            violations.append(f"reviewer error: {type(exc).__name__}")
        current = dict(self.hash_provider(self.capability.readable_files))
        for path in self.capability.readable_files:
            if current.get(path) != self.source_hashes[path]:
                violations.append(f"source hash mismatch: {path}")
        if violations:
            return ReviewOutcome(Verdict.INCONCLUSIVE, violations=tuple(violations))
        verdict = Verdict.FINDINGS if context.findings else Verdict.PASS
        return ReviewOutcome(verdict, findings=tuple(context.findings))
