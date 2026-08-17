import hashlib
import json
from pathlib import Path

import pytest

from prime_bridge.emergency_control import EmergencyController, EmergencyControlError
from prime_bridge.profile_policy import PrimeProfilePolicy


ROOT = Path(__file__).resolve().parents[2]


def setup(tmp_path: Path):
    policy = tmp_path / "prime-profile-policy.json"
    policy.write_bytes((ROOT / "config/prime-profile-policy.json").read_bytes())
    return policy, EmergencyController(policy, tmp_path / "backups")


def test_disable_and_exact_rollback_preserve_native_contract(tmp_path):
    policy, controller = setup(tmp_path)
    before = policy.read_bytes()
    receipt = controller.apply(
        expected_before_sha256=hashlib.sha256(before).hexdigest(),
        rollback_id="incident-one", confirmation="DISABLE-PRIME",
    )
    assert receipt["status"] == "disabled" and receipt["native_hermes"] == "AVAILABLE"
    assert PrimeProfilePolicy.load(policy).enabled is False
    restored = controller.rollback(rollback_id="incident-one", expected_current_sha256=receipt["after_sha256"])
    assert restored["status"] == "restored" and policy.read_bytes() == before


@pytest.mark.parametrize("failure", ["confirm", "hash", "verify", "already"])
def test_disable_failures_are_rejected_or_restore_exact_bytes(tmp_path, failure):
    policy, controller = setup(tmp_path)
    before = policy.read_bytes()
    expected = hashlib.sha256(before).hexdigest()
    if failure == "confirm":
        with pytest.raises(EmergencyControlError):
            controller.apply(expected_before_sha256=expected, rollback_id="one", confirmation="yes")
    elif failure == "hash":
        with pytest.raises(EmergencyControlError):
            controller.apply(expected_before_sha256="0" * 64, rollback_id="one", confirmation="DISABLE-PRIME")
    elif failure == "verify":
        receipt = controller.apply(expected_before_sha256=expected, rollback_id="one", confirmation="DISABLE-PRIME", verify=lambda _path: False)
        assert receipt["status"] == "rolled_back" and policy.read_bytes() == before
    else:
        value = json.loads(before)
        value["enabled"] = False
        policy.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(EmergencyControlError):
            controller.apply(expected_before_sha256=hashlib.sha256(policy.read_bytes()).hexdigest(), rollback_id="one", confirmation="DISABLE-PRIME")
