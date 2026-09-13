
import json
import os
from pathlib import Path

import pytest

from prime_bridge.adaptive_review.herdr_isolation import (
    HerdrIsolationError, HerdrReviewIsolation,
)


def isolation(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    return HerdrReviewIsolation(repository, "receipt", "attempt", "reviewer").create()


def test_isolation_is_outside_repository_and_disables_history(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "forbidden")
    item = isolation(tmp_path)
    try:
        assert item.root not in item.repository.resolve().parents
        assert item.repository.resolve() not in item.root.parents
        config = (item.root / "herdr-config.toml").read_text()
        assert "pane_history = false" in config
        assert "scrollback_limit_bytes = 0" in config
        env = item.environment()
        assert "SECRET_TOKEN" not in env
        assert env["LAPART_REVIEW_ROOT"] == str(item.root)
    finally:
        item.cleanup()


def test_request_is_atomic_and_result_rejects_symlink(tmp_path):
    item = isolation(tmp_path)
    try:
        item.write_request({"sealed": True})
        assert json.loads(item.request_path.read_text()) == {"sealed": True}
        target = item.root / "target.json"
        target.write_text("{}")
        try:
            item.result_path.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable")
        with pytest.raises(HerdrIsolationError, match="unsafe"):
            item.read_result()
    finally:
        item.cleanup()


def test_ambiguous_state_is_quarantined_until_explicit_reconciliation(tmp_path):
    item = isolation(tmp_path)
    root = item.root
    item.quarantine("delivery unknown")
    item.cleanup()
    assert root.exists()
    marker = json.loads((root / "AMBIGUOUS.json").read_text())
    assert marker["reason"] == "delivery unknown"


def test_reviewer_failure_marker_fails_wait_closed_immediately(tmp_path):
    item = isolation(tmp_path)
    try:
        item.failure_path.write_text(
            json.dumps({"returncode": 7, "output_accepted": False}),
            encoding="utf-8",
        )
        with pytest.raises(HerdrIsolationError, match="closed failure"):
            item.wait_result(timeout_ms=10_000, poll_ms=10)
    finally:
        item.cleanup()
