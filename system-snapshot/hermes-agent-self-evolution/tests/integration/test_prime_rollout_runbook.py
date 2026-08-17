from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runbook_covers_every_operator_boundary_and_one_backend():
    text = (ROOT / "docs/prime-rollout-operations.md").read_text(encoding="utf-8")
    for phrase in (
        "only Hermes backend", "thin Hermes OS client", "all six compatibility checks",
        "prime_rollout_status", "global or per-profile saturation", "DISABLE-PRIME",
        "EmergencyController.rollback", "restart boundary", "Native Hermes",
    ):
        assert phrase in text
    assert "Mutable tags" in text and "explicit" in text
