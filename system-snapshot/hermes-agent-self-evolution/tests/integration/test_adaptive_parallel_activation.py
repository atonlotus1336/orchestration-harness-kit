import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent / "governed-executor-runtime" / "src"))

from prime_bridge.adaptive_parallel_activation import (
    ActivationError,
    ActivationManager,
    _evidence_hmac_sha256,
    _record_sha256,
    source_binding,
)

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
AUTHORITY_KEY = b"test-qualification-authority-key"


def _manager(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_bytes((ROOT / "config" / "adaptive-parallel-policy.json").read_bytes())
    legacy = tmp_path / "legacy.json"
    legacy.write_bytes((ROOT / "config" / "additive-parallel-activation.json").read_bytes())
    return ActivationManager(
        policy_path=policy, legacy_path=legacy,
        qualification_path=tmp_path / "qualification.json",
        receipts_path=tmp_path / "receipts",
        authority_key=AUTHORITY_KEY,
    )


def _evidence(policy_path, *, layer="execution", scope="limited", hours=24, lineages=20,
              predecessor=None, qualification_predecessor=None):
    canonical = {
        "execution": "execution_parallelism",
        "reviewer": "reviewer_concurrency",
    }.get(layer, layer)
    capabilities = {
        "execution_parallelism": "isolated_coding",
        "reviewer_concurrency": "review",
        "career_kernel": "career_kernel",
        "temperament": "temperament",
        "career_shadow_pass": "career_shadow_pass",
    }
    evidence = {
        "schema_version": "2.0", "harness": "codex", "profile": "default",
        "model_route_sha256": "a" * 64, "workload_class": "coding",
        "capability": capabilities[canonical],
        "s_pipeline": None if canonical == "execution_parallelism" else "S3",
        "expires_at": "2030-02-01T00:00:00Z",
        "budgets": {
            "max_calls": 5, "max_tokens": 50000,
            "max_wall_seconds": 1800, "max_cost": 0,
        },
        "provider_free_passes": 3, "live_trials": 5, "live_failures": 0,
        "soak_hours": hours, "lineages": lineages, "multilineage_capacity": 2,
        "rollback_exercised": True, "expiry_exercised": True, "scope": scope,
        "policy_sha256": source_binding(policy_path)["policy_sha256"],
        "layer_ceiling": 2 if canonical in {"execution_parallelism", "reviewer_concurrency"} else 1,
        "matched_pairs": 5,
        "quality": {
            "mandatory_recall": 1.0, "precision": 1.0, "schema_validity": 1.0,
            "answerable_refusal_rate": 0.0, "p95_latency_ratio": 1.0,
        },
        "accounting": {
            "permission_bypasses": 0, "lost_work": 0, "duplicated_work": 0,
            "unsafe_publications": 0, "unaccounted_usage": 0,
            "missed_seeded_critical_defects": 0,
        },
        "qualification_predecessor_sha256": qualification_predecessor,
        "limited_predecessor_sha256": predecessor,
    }
    evidence["authority_hmac_sha256"] = _evidence_hmac_sha256(
        evidence, AUTHORITY_KEY,
    )
    return evidence


def _qualify_chain(manager, *, layer="execution", through="wider"):
    candidate = manager.qualify(
        layer=layer, qualification_state="candidate",
        evidence=_evidence(
            manager.policy_path, layer=layer, scope="canary",
            hours=0, lineages=0,
        ),
        now=NOW,
    )
    if through == "candidate":
        return candidate
    canary = manager.qualify(
        layer=layer, qualification_state="canary_qualified",
        evidence=_evidence(
            manager.policy_path, layer=layer, scope="canary",
            hours=0, lineages=0,
            qualification_predecessor=candidate["record_key"],
        ),
        now=NOW,
    )
    if through == "canary":
        return canary
    limited = manager.qualify(
        layer=layer, qualification_state="production_qualified",
        evidence=_evidence(
            manager.policy_path, layer=layer,
            qualification_predecessor=canary["record_key"],
        ),
        now=NOW,
    )
    if through == "limited":
        return limited
    return manager.qualify(
        layer=layer, qualification_state="production_qualified",
        evidence=_evidence(
            manager.policy_path, layer=layer, scope="wider",
            hours=72, lineages=50, predecessor=limited["record_key"],
            qualification_predecessor=limited["record_key"],
        ),
        now=NOW,
    )


def test_shipped_adaptive_policy_is_inactive_and_legacy_v1_is_preserved(tmp_path):
    status = _manager(tmp_path).status()
    assert status["state"] == "inactive"
    assert status["legacy_v1"] == {
        "state": "shadow_active", "max_concurrent_children": 2,
        "meaning_preserved": True,
    }


def test_plan_is_read_only_and_production_requires_both_soak_thresholds(tmp_path):
    manager = _manager(tmp_path)
    before = manager.policy_path.read_bytes()
    assert manager.plan(target="production", layers=("execution",))["writes_performed"] is False
    assert manager.policy_path.read_bytes() == before
    for hours, lineages in ((23, 20), (24, 19)):
        with pytest.raises(ActivationError, match="both time and lineage"):
            manager.qualify(
                layer="execution", qualification_state="production_qualified",
                evidence=_evidence(manager.policy_path, hours=hours, lineages=lineages), now=NOW,
            )


def test_independent_layer_grants_promote_and_rollback_without_refunding_liability(tmp_path):
    manager = _manager(tmp_path)
    _qualify_chain(manager)
    receipt = manager.promote(
        target="production", layers=("execution",), harness="codex",
        profile="default", model_route_sha256="a" * 64,
        workload_class="coding", now=NOW,
    )
    assert receipt["state"] == "production"
    policy = json.loads(manager.policy_path.read_text())
    assert policy["execution"]["enabled"] is True
    assert policy["reviewer"]["enabled"] is False
    rollback = manager.rollback(
        reason="adverse evidence", outstanding_liabilities={"child-1": 5}, now=NOW,
    )
    assert rollback["reconcile_liabilities_once"] == {"child-1": 5}
    assert rollback["admit_new_parallel_batches"] is False
    assert json.loads(manager.policy_path.read_text())["activation_state"] == "inactive"


def test_stale_source_binding_denies_promotion(tmp_path):
    manager = _manager(tmp_path)
    _qualify_chain(manager, layer="reviewer", through="canary")
    manager.policy_path.write_text(
        manager.policy_path.read_text().replace('"promotion_streak": 3', '"promotion_streak": 4')
    )
    with pytest.raises(ActivationError, match="no current"):
        manager.promote(
            target="review", layers=("reviewer",), harness="codex",
            profile="default", model_route_sha256="a" * 64,
            workload_class="coding", s_pipeline="S3", now=NOW,
        )


@pytest.mark.parametrize(
    ("layer", "wrong_capability"),
    (("execution", "review"), ("reviewer", "isolated_coding")),
)
def test_layer_capability_mismatch_is_rejected(tmp_path, layer, wrong_capability):
    manager = _manager(tmp_path)
    evidence = _evidence(manager.policy_path, layer=layer, hours=0, lineages=0)
    evidence["capability"] = wrong_capability
    with pytest.raises(ActivationError, match="qualification capability must be"):
        manager.qualify(
            layer=layer, qualification_state="candidate",
            evidence=evidence, now=NOW,
        )


def test_five_grants_are_independent_and_modifiers_require_career_kernel(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(ActivationError, match="career modifiers require"):
        manager.plan(target="review", layers=("temperament",))
    with pytest.raises(ActivationError, match="requires reviewer concurrency"):
        manager.plan(target="review", layers=("career_kernel",))
    plan = manager.plan(
        target="review",
        layers=("reviewer_concurrency", "career_kernel", "temperament"),
    )
    assert plan["layers"] == [
        "reviewer_concurrency", "career_kernel", "temperament",
    ]


def test_wider_production_requires_linked_limited_predecessor(tmp_path):
    manager = _manager(tmp_path)
    limited = _qualify_chain(manager, through="limited")
    manager.qualify(
        layer="execution_parallelism", qualification_state="production_qualified",
        evidence=_evidence(
            manager.policy_path, scope="wider", hours=72, lineages=50,
            predecessor=limited["record_key"],
            qualification_predecessor=limited["record_key"],
        ),
        now=NOW,
    )
    with pytest.raises(ActivationError, match="state transition is invalid"):
        manager.qualify(
            layer="reviewer_concurrency", qualification_state="production_qualified",
            evidence=_evidence(
                manager.policy_path, layer="reviewer_concurrency", scope="wider",
                hours=72, lineages=50, predecessor="f" * 64,
                qualification_predecessor="f" * 64,
            ),
            now=NOW,
        )


def test_recomputed_unkeyed_forgery_cannot_qualify(tmp_path):
    manager = _manager(tmp_path)
    evidence = _evidence(
        manager.policy_path, scope="canary", hours=0, lineages=0,
    )
    evidence["provider_free_passes"] = 999
    evidence["authority_hmac_sha256"] = "0" * 64
    with pytest.raises(ActivationError, match="authority signature"):
        manager.qualify(
            layer="execution", qualification_state="candidate",
            evidence=evidence, now=NOW,
        )


def test_transition_cannot_skip_canary_or_overwrite_immutable_records(tmp_path):
    manager = _manager(tmp_path)
    candidate = _qualify_chain(manager, through="candidate")
    with pytest.raises(ActivationError, match="state transition"):
        manager.qualify(
            layer="execution", qualification_state="production_qualified",
            evidence=_evidence(
                manager.policy_path,
                qualification_predecessor=candidate["record_key"],
            ),
            now=NOW,
        )
    store = json.loads(manager.qualification_path.read_text())
    assert store["schema_version"] == "2.1"
    assert list(store["records"]) == [candidate["record_key"]]


def test_canary_and_pilot_promotions_follow_exact_qualification_scope(tmp_path):
    manager = _manager(tmp_path)
    candidate = _qualify_chain(manager, through="candidate")
    receipt = manager.promote(
        target="canary", layers=("execution",), harness="codex",
        profile="default", model_route_sha256="a" * 64,
        workload_class="coding", now=NOW,
    )
    assert receipt["state"] == "canary"
    manager.rollback(reason="finish canary fixture", now=NOW)

    canary = manager.qualify(
        layer="execution", qualification_state="canary_qualified",
        evidence=_evidence(
            manager.policy_path, scope="canary", hours=0, lineages=0,
            qualification_predecessor=candidate["record_key"],
        ),
        now=NOW,
    )
    receipt = manager.promote(
        target="pilot_limited", layers=("execution",), harness="codex",
        profile="default", model_route_sha256="a" * 64,
        workload_class="coding", now=NOW,
    )
    assert receipt["state"] == "pilot_limited"
    manager.rollback(reason="finish limited pilot fixture", now=NOW)

    limited = manager.qualify(
        layer="execution", qualification_state="production_qualified",
        evidence=_evidence(
            manager.policy_path,
            qualification_predecessor=canary["record_key"],
        ),
        now=NOW,
    )
    assert manager.promote(
        target="pilot_wider", layers=("execution",), harness="codex",
        profile="default", model_route_sha256="a" * 64,
        workload_class="coding", now=NOW,
    )["state"] == "pilot_wider"
    assert limited["record"]["scope"] == "limited"


def test_operator_issued_candidate_is_accepted_by_execution_runtime(tmp_path):
    from prime_bridge.mcp_server import PrimeToolService
    from lapart_executor.adaptive_parallel import AdaptiveParallelPolicy

    manager = _manager(tmp_path)
    candidate = _qualify_chain(manager, through="candidate")
    manager.promote(target="canary", layers=("execution",), harness="codex",
                    profile="default", model_route_sha256="a" * 64,
                    workload_class="coding", now=NOW)
    # Use the real verifier without starting provider, ledger or worker services.
    service = object.__new__(PrimeToolService)
    service.profile = "default"
    service.adaptive_policy_path = manager.policy_path
    service.adaptive_qualification_path = manager.qualification_path
    service.adaptive_authority_key = AUTHORITY_KEY
    policy = AdaptiveParallelPolicy.load(manager.policy_path)
    facts = dict(policy=policy, harness="codex", workload_class="coding",
                 model_route_sha256="a" * 64, now=NOW)
    assert service._trusted_execution_qualification(**facts) == candidate["record"]
    assert service._trusted_execution_qualification(**{**facts, "harness": "hermes"}) is None
    assert service._trusted_execution_qualification(**{**facts, "model_route_sha256": "b" * 64}) is None
