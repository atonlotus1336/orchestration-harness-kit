
import hashlib
import json
from pathlib import Path

import pytest

from prime_bridge.adaptive_review.controller import Controller
from prime_bridge.adaptive_review.herdr_transport import HerdrReviewTransport
from prime_bridge.adaptive_review.transport import BudgetViolation, JsonReviewTransport
from prime_bridge.adaptive_review.transport_registry import (
    is_admitted_report_only_transport,
)

ROOT = Path(__file__).resolve().parents[2]


def test_json_and_herdr_are_sealed_builtins_but_shipped_selection_is_off():
    assert is_admitted_report_only_transport(
        JsonReviewTransport("http://127.0.0.1:1/v1/chat/completions", "fixture")
    )
    config = json.loads((ROOT / "config/herdr-evaluator-adapters.json").read_text())
    assert config["mode"] == "off"
    assert config["reviewers"] == {}
    assert config["targets"]["wsl"]["qualified"] is False


def test_inactive_factory_refuses_before_client_or_session_creation(tmp_path):
    config = tmp_path / "herdr.json"
    config.write_text(json.dumps({
        "schema_version": "1.0", "mode": "off",
        "targets": {"windows": {}, "wsl": {}}, "reviewers": {},
    }))
    with pytest.raises(BudgetViolation, match="inactive or unqualified"):
        HerdrReviewTransport.from_configuration(
            config, repository=tmp_path, receipt_id="r", attempt_id="a",
            reviewer_id="reviewer", s_level="S1", source_identity="source",
        )


def test_controller_accepts_transport_selection_fields_without_changing_default(tmp_path):
    skill = tmp_path / "skill.md"
    skill.write_text("review")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "schema_version": "1.0",
        "reviewers": [{
            "reviewer_id": "reviewer", "lens": "changed_scope_simplicity",
            "triggers": [], "skill_path": "skill.md",
            "skill_sha256": hashlib.sha256(skill.read_bytes()).hexdigest(),
            "requires_confirmation": False,
        }],
    }))
    config = {
        "schema_version": "1.0", "max_deliveries": 2,
        "harness_modes": {"codex": "shadow"},
        "approved_workspaces": [str(tmp_path)],
        "provider": {
            "endpoint": "http://127.0.0.1:1/v1/chat/completions",
            "model": "fixture",
        },
        "reviewer_transports": {},
        "herdr_evaluator_config": str(ROOT / "config/herdr-evaluator-adapters.json"),
    }
    controller = Controller(
        state_root=tmp_path / "state", vault_root=tmp_path,
        catalog_path=catalog, config=config,
    )
    assert controller.config["reviewer_transports"] == {}
    assert controller.config["provider"]["model"] == "fixture"
