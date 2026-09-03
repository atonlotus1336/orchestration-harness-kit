import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / "docs" / "v2" / "provenance-map.json"
SCRIPT_PATH = ROOT / "tools" / "verify-v2-provenance.py"

SPEC = importlib.util.spec_from_file_location("verify_v2_provenance", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def load_map():
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def test_canonical_map_passes():
    assert MODULE.validate(load_map()) == []


def test_missing_secret_exclusion_fails_closed():
    data = load_map()
    data["mandatory_exclusions"].remove("credentials")
    assert MODULE.validate(data)


def test_false_v1_privacy_pass_fails_closed():
    data = load_map()
    data["v1_baseline"]["privacy_gate"] = "PASS"
    assert MODULE.validate(data)


def test_working_tree_import_fails_closed():
    data = load_map()
    data["sources"][0]["import_from"] = "working_tree"
    assert MODULE.validate(data)
