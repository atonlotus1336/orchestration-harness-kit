"""Compatibility observer facade over the portable unified Orca switch."""
from pathlib import Path

from adaptive_switch import PortableSwitchError
from orchestration_switch import HARNESS_REGISTRY, PortableOrchestrationSwitch

MODE = "unified_orca"


class PortableOrcaObserverSwitch:
    def __init__(self, state_root, registry=HARNESS_REGISTRY):
        self.owner = PortableOrchestrationSwitch(state_root, registry)
        self.root, self.registry, self.path = self.owner.root, self.owner.registry, self.owner.path

    def status(self):
        value = self.owner.status("orca", self.registry)
        row = value["features"]["orca"]
        return {
            "global": row["global"], "mode": MODE,
            "enabled_harnesses": [name for name, mode in row["harnesses"].items() if mode != "off"],
            "consumer_required": True,
            "authority": {"canonical_control": "orchestration-switches.json"},
        }

    def set(self, targets, *, turn_on):
        selected = tuple(targets)
        if turn_on:
            result = self.owner.set("orca", "observe", selected)
        else:
            modes = self.owner.status("orca", selected)["features"]["orca"]["harnesses"]
            if any(mode not in {"observe", "off"} for mode in modes.values()):
                return {"changed": False, "changed_harnesses": [], "result": "DRAIN_REQUIRED", "after": self.status()}
            result = self.owner.set("orca", "off", selected, verified_orca_off=True)
        return {
            "changed": result.get("changed", False),
            "changed_harnesses": [cell["harness"] for cell in result.get("changed_cells", [])],
            "result": self.status()["global"], "after": self.status(),
        }


def enabled_harnesses(state_path, registry=HARNESS_REGISTRY):
    path = Path(state_path)
    owner = PortableOrcaObserverSwitch(path.parent, registry)
    if owner.path != path:
        raise PortableSwitchError("Orca state filename is invalid")
    return frozenset(owner.status()["enabled_harnesses"])
