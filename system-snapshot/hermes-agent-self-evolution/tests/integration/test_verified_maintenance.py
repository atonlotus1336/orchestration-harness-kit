from pathlib import Path
import pytest
from prime_bridge.maintenance import CandidateResult, MaintenanceController, MaintenancePolicyError
from prime_bridge.maintenance_ledger import MaintenanceLedger


ROOT = Path(__file__).resolve().parents[2]


class Adapter:
    def __init__(self, results, restore_ok=True): self.results, self.restored, self.restore_ok = results, [], restore_ok
    def snapshot(self, component): return {"component": component, "ref": "known-good"}
    def update_candidate(self, component): return self.results[component]
    def restore(self, component, snapshot): self.restored.append(component); return self.restore_ok


def controller(tmp_path, results, restore_ok=True):
    return MaintenanceController(policy_path=ROOT / "config/verified-maintenance-policy.json", ledger=MaintenanceLedger(tmp_path / "ledger.sqlite3"), adapter=Adapter(results, restore_ok))


def test_all_components_promote(tmp_path):
    good = CandidateResult(True, True, True, True)
    run = controller(tmp_path, {"hermes": good, "prime": good, "omp": good}).run(run_id="all")
    assert [event["outcome"] for event in run["events"]] == ["PROMOTED"] * 3


def test_failure_restores_then_continues(tmp_path):
    good = CandidateResult(True, True, True, True)
    bad = CandidateResult(True, False, False, False)
    run = controller(tmp_path, {"hermes": bad, "prime": good, "omp": good}).run(run_id="continue")
    assert [event["outcome"] for event in run["events"]] == ["FAILED_RESTORED", "PROMOTED", "PROMOTED"]


@pytest.mark.parametrize("failed_component,expected", [
    ("prime", ["PROMOTED", "FAILED_RESTORED", "PROMOTED"]),
    ("omp", ["PROMOTED", "PROMOTED", "FAILED_RESTORED"]),
])
def test_later_independent_failure_restores_without_rewriting_prior_promotions(tmp_path, failed_component, expected):
    good = CandidateResult(True, True, True, True)
    results = {"hermes": good, "prime": good, "omp": good}
    results[failed_component] = CandidateResult(True, False, False, False)
    run = controller(tmp_path, results).run(run_id=failed_component)
    assert [event["outcome"] for event in run["events"]] == expected


def test_unsafe_failure_halts(tmp_path):
    good = CandidateResult(True, True, True, True)
    bad = CandidateResult(True, False, False, False, halt_class="INTEGRITY_FAILURE")
    run = controller(tmp_path, {"hermes": good, "prime": bad, "omp": good}).run(run_id="halt")
    assert run["halted"] and run["events"][-1]["halt_class"] == "INTEGRITY_FAILURE"


def test_failed_rollback_halts_and_does_not_attempt_later_components(tmp_path):
    good = CandidateResult(True, True, True, True)
    failed_commit = CandidateResult(True, True, True, False)
    run = controller(tmp_path, {"hermes": failed_commit, "prime": good, "omp": good}, restore_ok=False).run(run_id="rollback")
    assert run["halted"]
    assert run["events"][-1]["halt_class"] == "ROLLBACK_UNVERIFIED"
    assert len(run["events"]) == 1


def test_live_mode_is_rejected_until_a_reviewed_adapter_exists(tmp_path):
    good = CandidateResult(True, True, True, True)
    with pytest.raises(MaintenancePolicyError):
        controller(tmp_path, {"hermes": good, "prime": good, "omp": good}).run(dry_run=False)
