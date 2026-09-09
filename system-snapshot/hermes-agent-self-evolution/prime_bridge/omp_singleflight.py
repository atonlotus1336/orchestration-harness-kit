"""Cross-process single-flight ownership for the rootless OMP sidecar.

Competing sidecars fail closed.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class OmpSingleFlightError(RuntimeError):
    pass


@contextmanager
def omp_singleflight_lock(path: Path = Path("/tmp/hermes-omp-sidecar.lock")) -> Iterator[None]:
    """Fail closed rather than run a competing readiness or OMP sidecar."""
    if os.name != "posix":
        yield
        return
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OmpSingleFlightError("OMP sidecar already active") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
