
import hashlib

from prime_bridge.adaptive_review.adapters import (
    ProviderAttestation,
    ReportOnlyProvider,
    ReviewerAdapter,
    ReviewerCapability,
)
from prime_bridge.adaptive_review.models import Verdict
from prime_bridge.adaptive_review.transport import (
    JsonReviewTransport,
    ReportOnlyReviewTransport,
    ReviewBudgetTracker,
)
from prime_bridge.adaptive_review.transport_registry import (
    is_admitted_report_only_transport,
)


class JsonSubclass(JsonReviewTransport):
    pass


class DuckTransport:
    def validate(self):
        return None

    def payload(self, skill, contents, output_cap):
        return b"{}"

    def run(self, skill, contents, budget):
        return ()


def budget():
    return ReviewBudgetTracker({
        "max_context_bytes": 4096,
        "max_model_calls": 1,
        "max_estimated_input_tokens": 4096,
        "max_tool_calls": 0,
        "max_elapsed_seconds": 30,
        "max_output_tokens": 256,
        "max_total_tokens": 4096,
    })


def test_protocol_is_descriptive_but_admission_is_explicit_and_sealed():
    owned = JsonReviewTransport("http://127.0.0.1:1/v1/chat/completions", "test")
    assert isinstance(owned, ReportOnlyReviewTransport)
    assert is_admitted_report_only_transport(owned)
    assert not is_admitted_report_only_transport(JsonSubclass(owned.endpoint, owned.model))
    assert isinstance(DuckTransport(), ReportOnlyReviewTransport)
    assert not is_admitted_report_only_transport(DuckTransport())


def test_unknown_transport_is_refused_before_source_observation():
    body = "print('ok')"
    digest = hashlib.sha256(body.encode()).hexdigest()
    observed = []
    adapter = ReviewerAdapter(
        ReviewerCapability(("src/app.py",)),
        contents={"src/app.py": body},
        source_hashes={"src/app.py": digest},
        hash_provider=lambda paths: observed.append(paths) or {"src/app.py": digest},
    )
    outcome = adapter.run(
        ReportOnlyProvider(
            attestation=ProviderAttestation(),
            transport=DuckTransport(),
        ),
        budget=budget(),
        skill="review",
    )
    assert outcome.verdict is Verdict.INCONCLUSIVE
    assert "unmetered" in outcome.violations[0]
    assert observed == []


def test_herdr_is_not_registered_during_disconnected_build():
    assert not is_admitted_report_only_transport(None)
    assert "Herdr" not in repr(JsonReviewTransport)
