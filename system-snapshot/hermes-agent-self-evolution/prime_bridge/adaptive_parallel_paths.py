"""Host-local adaptive state selection, separate from distributable defaults."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_STATE = ROOT / ".state" / "adaptive-parallel"
SHIPPED_POLICY = ROOT / "config" / "adaptive-parallel-policy.json"


def policy_path(explicit=None) -> Path:
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get("LAPART_ADAPTIVE_POLICY_PATH")
    if override:
        return Path(override)
    local = LOCAL_STATE / "policy.json"
    # Invalid local state is selected too: consumers must fail closed.
    return local if local.exists() else SHIPPED_POLICY


def qualification_path(explicit=None) -> Path:
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get("LAPART_ADAPTIVE_QUALIFICATION_PATH")
    if override:
        return Path(override)
    if (LOCAL_STATE / "policy.json").exists():
        return LOCAL_STATE / "qualification.json"
    return ROOT / ".planning" / "adaptive-parallel-qualification.json"


def receipts_path(explicit=None) -> Path:
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get("LAPART_ADAPTIVE_RECEIPTS_PATH")
    if override:
        return Path(override)
    if (LOCAL_STATE / "policy.json").exists():
        return LOCAL_STATE / "receipts"
    return ROOT / ".planning" / "adaptive-parallel-activation-receipts"
