from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "modules" / "adapter-sdk"
CORE = ROOT / "modules" / "orchestration-core"
PROTOCOL = ROOT / "modules" / "orchestration-protocol"
sys.path.insert(0, str(SDK))
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT))

from lapart_adapter import HarnessManifest, ManifestError, UniversalHarnessBridge
from lapart_adapter.reference import JsonReferenceAdapter
from lapart_core import OrchestrationCore


def manifest() -> HarnessManifest:
    return HarnessManifest.load(
        SDK / "templates/harness-manifest.json",
        catalog_path=PROTOCOL / "operation-catalog.json",
    )


def test_reference_manifest_is_closed_and_negotiates_all_operations():
    value = manifest()
    assert value.protocol_versions == (1, 2)
    assert len(value.operations) == 31
    assert value.adapter_sdk_version == 2


def test_reference_manifest_conforms_to_public_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SDK / "harness-manifest.schema.json").read_text(encoding="utf-8"))
    instance = json.loads((SDK / "templates/harness-manifest.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(instance)


def test_reference_bridge_preserves_core_accounting_and_lane():
    value = manifest()
    bridge = UniversalHarnessBridge(
        manifest=value,
        adapter=JsonReferenceAdapter(value),
        core=OrchestrationCore.from_module(CORE),
    )
    result = bridge.dispatch(
        {
            "operation": "routing.prime.select",
            "payload": {"task": {"requires_omp": True}, "task_tier": "atomic"},
            "session_id": "test-session",
            "session_state": "active",
        }
    )
    assert result.envelope["payload"]["recommended_lane"] == "T2"
    assert result.envelope["payload"]["actual_lane"] == "T3"
    assert result.provider_accounting["provider_calls"] == 0
    assert result.local_response["provider_accounting"] == result.provider_accounting


def test_reference_bridge_exercises_stable_lifecycle_methods():
    value = manifest()
    bridge = UniversalHarnessBridge(
        manifest=value,
        adapter=JsonReferenceAdapter(value),
        core=OrchestrationCore.from_module(CORE),
    )
    assert bridge.initialize(session_id="lifecycle").envelope["payload"]["selected_version"] == 2
    assert bridge.shutdown(session_id="lifecycle", reason="test").envelope["payload"]["state"] == "closed"


def test_manifest_rejects_core_policy_field(tmp_path):
    value = json.loads((SDK / "templates/harness-manifest.json").read_text(encoding="utf-8"))
    value["core_policy"] = {"routing": "adapter"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        HarnessManifest.load(path, catalog_path=PROTOCOL / "operation-catalog.json")
    assert caught.value.code == "MANIFEST_INVALID"


@pytest.mark.parametrize("entrypoint", ["../adapter.py", "/tmp/adapter.py", "folder\\adapter.py"])
def test_manifest_rejects_nonportable_entrypoint(tmp_path, entrypoint):
    value = json.loads((SDK / "templates/harness-manifest.json").read_text(encoding="utf-8"))
    value["adapter"]["entrypoint"] = entrypoint
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        HarnessManifest.load(path, catalog_path=PROTOCOL / "operation-catalog.json")
    assert caught.value.code == "ADAPTER_PATH_INVALID"


def test_manifest_rejects_credential_value_field(tmp_path):
    value = json.loads((SDK / "templates/harness-manifest.json").read_text(encoding="utf-8"))
    value["configuration"]["authentication"]["secret"] = "forbidden"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        HarnessManifest.load(path, catalog_path=PROTOCOL / "operation-catalog.json")
    assert caught.value.code == "POLICY_OR_CREDENTIAL_DENIED"


def test_bridge_rejects_unnegotiated_operation(tmp_path):
    raw = json.loads((SDK / "templates/harness-manifest.json").read_text(encoding="utf-8"))
    raw["operations"] = {"mode": "subset", "include": ["health.readiness"], "exclude": []}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    value = HarnessManifest.load(path, catalog_path=PROTOCOL / "operation-catalog.json")
    bridge = UniversalHarnessBridge(
        manifest=value,
        adapter=JsonReferenceAdapter(value),
        core=OrchestrationCore.from_module(CORE),
    )
    with pytest.raises(ManifestError) as caught:
        bridge.dispatch({"operation": "tool.discover", "payload": {"query": ""}, "session_id": "test", "session_state": "active"})
    assert caught.value.code == "OPERATION_NOT_NEGOTIATED"


def test_provider_free_verifier_covers_bridge_and_mutations():
    spec = importlib.util.spec_from_file_location("adapter_verifier", SDK / "verify-adapter-sdk.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.verify_bridge() == []
    assert module.verify_template_boundary() == []
    assert module.verify_negative_cases() == []


def test_module_manifest_installs_adapter_sdk_after_core():
    value = json.loads((ROOT / "installer/modules/module-manifest.json").read_text(encoding="utf-8"))
    entries = {row["id"]: row for row in value["modules"]}
    assert value["default_path"][:3] == ["orchestration-protocol", "orchestration-core", "adapter-sdk"]
    assert entries["adapter-sdk"]["stage"] == 2
    assert entries["adapter-sdk"]["required"] is True
