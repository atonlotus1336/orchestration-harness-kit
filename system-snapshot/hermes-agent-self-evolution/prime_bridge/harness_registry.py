"""Canonical harness names shared by portable orchestration controls."""
from __future__ import annotations


UNIVERSAL_HARNESS_REGISTRY = (
    "codex",
    "hermes",
    "claude-code",
    "moa",
    "cursor",
    "dsh",
    "pi",
    "opencode",
    "antigravity",
)


class HarnessRegistryError(ValueError):
    pass


def resolve_universal_targets(*, global_selector: bool,
                              harnesses) -> tuple[str, ...]:
    selected = tuple(harnesses or ())
    if global_selector and selected:
        raise HarnessRegistryError("--global cannot be combined with --harness")
    if not global_selector and not selected:
        raise HarnessRegistryError("exactly one target selector is required")
    targets = UNIVERSAL_HARNESS_REGISTRY if global_selector else tuple(
        dict.fromkeys(selected)
    )
    unknown = set(targets) - set(UNIVERSAL_HARNESS_REGISTRY)
    if unknown:
        raise HarnessRegistryError(
            "unsupported harness: " + ", ".join(sorted(unknown))
        )
    return tuple(name for name in UNIVERSAL_HARNESS_REGISTRY if name in targets)


__all__ = [
    "HarnessRegistryError",
    "UNIVERSAL_HARNESS_REGISTRY",
    "resolve_universal_targets",
]
