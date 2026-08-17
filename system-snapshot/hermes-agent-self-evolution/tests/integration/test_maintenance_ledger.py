from pathlib import Path
import pytest
from prime_bridge.maintenance_ledger import MaintenanceLedger, MaintenanceLedgerError


def test_ledger_records_snapshot_and_outcome(tmp_path: Path):
    ledger = MaintenanceLedger(tmp_path / "ledger.sqlite3")
    snap = ledger.snapshot("run", "hermes", {"ref": "abc"})
    event = ledger.record("run", "hermes", outcome="PROMOTED", data={"restore_id": snap["restore_id"]})
    assert event["outcome"] == "PROMOTED"
    assert ledger.events("run")[0]["event_id"] == event["event_id"]


def test_ledger_rejects_secret_shaped_metadata(tmp_path: Path):
    ledger = MaintenanceLedger(tmp_path / "ledger.sqlite3")
    with pytest.raises(MaintenanceLedgerError):
        ledger.snapshot("run", "prime", {"api_key": "nope"})
