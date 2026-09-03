import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "installer" / "contracts" / "version-policy.json"
PRIVACY_PATH = ROOT / "installer" / "contracts" / "v1-privacy-classification.json"
SCRIPT_PATH = ROOT / "tools" / "verify-v1-compatibility.py"
SPEC = importlib.util.spec_from_file_location("verify_v1_compatibility", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_privacy():
    return json.loads(PRIVACY_PATH.read_text(encoding="utf-8"))


def test_policy_preserves_current_v1_cli():
    assert MODULE.validate(
        load_policy(), MODULE.current_commands(), MODULE.current_sdk_version()
    ) == []


def test_removed_v1_command_fails_closed():
    commands = MODULE.current_commands()
    commands.remove("doctor")
    assert MODULE.validate(load_policy(), commands)


def test_unsafe_v1_tag_promotion_fails_closed():
    policy = load_policy()
    policy["v1"]["tag_status"] = "READY"
    assert MODULE.validate(policy, MODULE.current_commands())


def test_recipient_data_regression_fails_closed():
    policy = copy.deepcopy(load_policy())
    policy["migration"]["preserve_recipient_data"] = False
    assert MODULE.validate(policy, MODULE.current_commands())


def test_historical_privacy_classification_reconciles():
    assert MODULE.validate_privacy(load_privacy()) == []


def test_selected_file_privacy_exemption_fails_closed():
    classification = load_privacy()
    classification["selected_file_exemptions"] = 1
    assert MODULE.validate_privacy(classification)


def test_installer_version_mismatch_fails_closed():
    policy = load_policy()
    policy["sdk_release"] = "9.0.0"
    assert MODULE.validate(policy, MODULE.current_commands(), MODULE.current_sdk_version())
