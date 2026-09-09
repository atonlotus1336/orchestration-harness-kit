"""Machine and path routing for the Prime/OMP execution boundary."""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass


NUCBOX = "alice"
THIN_CLIENT = "thin-client"
NUCBOX_TAILSCALE = "__TAILSCALE_IPV4__"
NUCBOX_WINDOWS_SSH_USER = "devbox"
NUCBOX_WSL_USER = "jonvilario"


def _canonical_machine(value: str | None) -> str:
    return (value or os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or socket.gethostname() or "").strip().upper()


def _machine_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value)


@dataclass(frozen=True)
class ExecutionTarget:
    machine: str
    kind: str
    remote_host: str | None = None


def execution_target(machine: str | None = None) -> ExecutionTarget:
    """Resolve the only supported execution owners without probing WSL."""
    normalized = _canonical_machine(machine)
    if _machine_key(normalized) == _machine_key(NUCBOX):
        return ExecutionTarget(machine=normalized or NUCBOX, kind="local-wsl")
    if _machine_key(normalized) == _machine_key(THIN_CLIENT):
        return ExecutionTarget(machine=normalized or THIN_CLIENT, kind="remote-required", remote_host=NUCBOX_TAILSCALE)
    return ExecutionTarget(machine=normalized or "UNKNOWN", kind="unsupported")


def remote_executor_result(machine: str | None = None) -> dict[str, object]:
    target = execution_target(machine)
    return {
        "status": "REMOTE_EXECUTOR_REQUIRED",
        "outcome": "blocked",
        "reason": "REMOTE_EXECUTOR_REQUIRED",
        "machine": target.machine,
        "executor": "alice",
        "transport": "ssh-over-tailscale",
        "governance_route_id": "desktop-prime-ssh",
        "remote_host": target.remote_host,
        "remote_port": 22,
        "remote_user": NUCBOX_WINDOWS_SSH_USER,
        "remote_runtime": {"distribution": "__WSL_DISTRO__", "user": NUCBOX_WSL_USER},
        "local_wsl_allowed": False,
        "governance_allowlisted": target.kind == "remote-required",
    }


def windows_path_to_wsl(path: str) -> str:
    """Translate drive-letter and Gdrive UNC paths without touching the network."""
    value = str(path).replace("\\", "/")
    drive = re.match(r"^([A-Za-z]):/(.*)$", value)
    if drive:
        return f"/mnt/{drive.group(1).lower()}/{drive.group(2)}"
    unc = re.match(r"^//[^/]+/Gdrive(?:/(.*))?$", value, re.IGNORECASE)
    if unc:
        suffix = unc.group(1) or ""
        return f"/mnt/g/{suffix}".rstrip("/") or "/mnt/g"
    raise ValueError(f"unsupported Windows path for WSL translation: {path!r}")
