import pytest

from prime_bridge.operations import OperationsError, REQUIRED_AXES, RolloutDashboard


def axes(status="PASS"):
    return {name: {"status": status, "reason": f"controlled-{name}"} for name in REQUIRED_AXES}


def test_all_axes_are_required_for_ready_and_native_stays_available():
    dashboard = RolloutDashboard()
    ready = dashboard.evaluate(axes(), emergency_disabled=False)
    assert ready["status"] == "READY" and ready["unready_axes"] == []
    value = axes()
    value["remote_client_reachability"] = {"status": "DEFERRED", "reason": "desktop-offline"}
    degraded = dashboard.evaluate(value, emergency_disabled=False)
    assert degraded["status"] == "DEGRADED" and degraded["unready_axes"] == ["remote_client_reachability"]
    assert degraded["native_hermes"] == "AVAILABLE" and degraded["one_backend"] is True


def test_emergency_disable_is_explicit_not_fake_ready():
    result = RolloutDashboard().evaluate(axes(), emergency_disabled=True)
    assert result["status"] == "DISABLED" and result["native_hermes"] == "AVAILABLE"


def test_missing_or_ambiguous_axis_fails_closed():
    value = axes()
    value.pop("model_auth_readiness")
    with pytest.raises(OperationsError):
        RolloutDashboard().evaluate(value, emergency_disabled=False)
    value = axes()
    value["bridge_liveness"] = {"status": "OK", "reason": "ambiguous"}
    with pytest.raises(OperationsError):
        RolloutDashboard().evaluate(value, emergency_disabled=False)
