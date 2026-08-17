import json
from pathlib import Path

from prime_bridge.profile_policy import PrimeProfilePolicy


ROOT = Path(__file__).resolve().parents[2]


def test_central_policy_selects_workspace_without_broad_conversion():
    policy = PrimeProfilePolicy.load(ROOT / "config/prime-profile-policy.json")
    assert policy.default_mode == "prime-available"
    assert policy.mode_for("workspace") == "prime-default"
    assert policy.mode_for("home") == "prime-available"
    assert policy.mode_for("telegram") == "prime-available"
    assert policy.explicit_handoff_required is True
    assert policy.native_fallback is True and policy.activation == "restart-boundary"


def test_runbook_covers_full_control_surface_and_one_backend():
    text = (ROOT / "docs/prime-backed-sessions.md").read_text(encoding="utf-8")
    for tool in (
        "prime_session_route", "prime_session_start", "prime_session_status",
        "prime_session_events", "prime_session_steer", "prime_session_approve",
        "prime_session_interrupt", "prime_session_detach", "prime_session_reattach",
    ):
        assert tool in text
    assert "does not run another Hermes backend" in text
    assert "explicit_handoff=true" in text
    assert "raw Prime trace" in text
    assert "__OWNER_HANDLE__ desktop is a thin Hermes OS client" in text


def test_policy_file_has_no_provider_or_secret_copy():
    value = json.loads((ROOT / "config/prime-profile-policy.json").read_text(encoding="utf-8"))
    encoded = json.dumps(value).lower()
    assert "api_key" not in encoded and "password" not in encoded and "bearer" not in encoded
    assert "provider" not in value and "model" not in value
