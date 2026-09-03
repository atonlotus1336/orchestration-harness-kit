"""Load the installed, provider-free Cursor bridge from the SDK tree."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
KIT_ROOT = PLUGIN_ROOT.parents[3]
SDK = KIT_ROOT / "modules/adapter-sdk"
CORE = KIT_ROOT / "modules/orchestration-core"
PROTOCOL = KIT_ROOT / "modules/orchestration-protocol"
TOKEN_COMPUTE = KIT_ROOT / "modules/token-compute-optimization"
sys.path[:0] = [str(KIT_ROOT), str(SDK), str(CORE), str(TOKEN_COMPUTE)]

from lapart_adapter import HarnessManifest, UniversalHarnessBridge
from lapart_core import OrchestrationCore


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def build_bridge() -> UniversalHarnessBridge:
    module_root = KIT_ROOT / "modules/adapters/cursor"
    manifest = HarnessManifest.load(
        module_root / "harness-manifest.json",
        catalog_path=PROTOCOL / "operation-catalog.json",
    )
    adapter_module = load(module_root / "cursor_adapter.py", "cursor_plugin_adapter")
    adapter = adapter_module.CursorAdapter(manifest)
    return UniversalHarnessBridge(
        manifest=manifest, adapter=adapter, core=OrchestrationCore.from_module(CORE),
    )
