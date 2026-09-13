
"""Content-only temporary roots for Herdr-hosted review processes."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Mapping


class HerdrIsolationError(RuntimeError):
    pass


_ALLOWED_ENV = frozenset({
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG", "LC_ALL",
})


@dataclass
class HerdrReviewIsolation:
    repository: Path
    receipt_id: str
    attempt_id: str
    reviewer_id: str
    root: Path | None = None
    ambiguous: bool = False

    def create(self) -> "HerdrReviewIsolation":
        repository = self.repository.resolve()
        root = Path(tempfile.mkdtemp(prefix="lapart-herdr-review-")).resolve()
        if root == repository or repository in root.parents or root in repository.parents:
            shutil.rmtree(root, ignore_errors=True)
            raise HerdrIsolationError("review root overlaps repository")
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        config = root / "herdr-config.toml"
        config.write_text("[advanced]\npane_history = false\nscrollback_limit_bytes = 0\n", encoding="utf-8")
        self.root = root
        return self

    def environment(self) -> dict[str, str]:
        if self.root is None:
            raise HerdrIsolationError("isolation root is not created")
        env = {key: value for key, value in os.environ.items() if key in _ALLOWED_ENV}
        env["HERDR_CONFIG_PATH"] = str(self.root / "herdr-config.toml")
        env["LAPART_REVIEW_ROOT"] = str(self.root)
        return env

    @property
    def request_path(self) -> Path:
        if self.root is None:
            raise HerdrIsolationError("isolation root is not created")
        return self.root / "request.json"

    @property
    def result_path(self) -> Path:
        if self.root is None:
            raise HerdrIsolationError("isolation root is not created")
        return self.root / "result.json"

    @property
    def failure_path(self) -> Path:
        if self.root is None:
            raise HerdrIsolationError("isolation root is not created")
        return self.root / "failure.json"

    def write_request(self, request: Mapping[str, Any]) -> None:
        if self.root is None:
            raise HerdrIsolationError("isolation root is not created")
        if any(path.is_symlink() for path in (self.root, self.request_path)):
            raise HerdrIsolationError("symlinked review state is forbidden")
        temporary = self.root / ".request.tmp"
        temporary.write_text(
            json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.request_path)

    def read_result(self, *, max_bytes: int = 1_048_576) -> Mapping[str, Any]:
        path = self.result_path
        if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
            raise HerdrIsolationError("review result is absent or unsafe")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise HerdrIsolationError("review result is malformed") from exc
        if not isinstance(value, Mapping):
            raise HerdrIsolationError("review result must be an object")
        return value

    def wait_result(self, *, timeout_ms: int, poll_ms: int = 50) -> Mapping[str, Any]:
        if timeout_ms <= 0 or poll_ms <= 0:
            raise HerdrIsolationError("invalid review result deadline")
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if self.result_path.is_file():
                return self.read_result()
            if self.failure_path.is_file():
                raise HerdrIsolationError("reviewer process reported a closed failure")
            time.sleep(min(poll_ms / 1000, max(0, deadline - time.monotonic())))
        raise HerdrIsolationError("review result deadline exceeded")

    def quarantine(self, reason: str) -> None:
        if self.root is None:
            return
        self.ambiguous = True
        marker = self.root / "AMBIGUOUS.json"
        marker.write_text(
            json.dumps({"reason": reason[:128]}, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def cleanup(self) -> None:
        if self.root is not None and not self.ambiguous:
            shutil.rmtree(self.root)
            self.root = None


__all__ = ["HerdrIsolationError", "HerdrReviewIsolation"]
