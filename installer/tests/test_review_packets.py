"""Lossless packet admission; synthetic requests, no provider calls."""
import hashlib
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'modules/orchestration-core'))
from lapart_core.review.packets import admit_packets, PacketRefused


def test_complete_set_admitted_without_mutation():
    packets = {"a": b"one", "b": b"two"}
    result = admit_packets(packets, max_request_bytes=3, max_input_units=6, max_calls=2)
    assert result["request_count"] == 2
    assert result["estimated_input_units"] == 6
    assert result["packet_sha256"]["a"] == hashlib.sha256(b"one").hexdigest()
    assert packets == {"a": b"one", "b": b"two"}


@pytest.mark.parametrize("limits,reason", [
    ({"max_request_bytes": 2}, "packet exceeds byte budget"),
    ({"max_input_units": 5}, "aggregate estimated input budget"),
    ({"max_calls": 1}, "model-call budget"),
])
def test_complete_set_refused_not_truncated(limits, reason):
    values = dict(max_request_bytes=3, max_input_units=6, max_calls=2)
    values.update(limits)
    with pytest.raises(PacketRefused, match=reason) as caught:
        admit_packets({"a": b"one", "b": b"two"}, **values)
    assert caught.value.report["request_count"] == 2
    assert caught.value.report["estimated_input_units"] == 6
    assert caught.value.report["admitted"] is False


@pytest.mark.parametrize("packets", [{}, {"a": "not bytes"}, {"": b"one"}, {"a": b""}])
def test_empty_or_invalid_input_is_not_a_pass(packets):
    with pytest.raises(ValueError):
        admit_packets(packets, max_request_bytes=100, max_input_units=100, max_calls=3)


def test_utf8_and_serialization_are_counted_not_character_estimates():
    payload = '"海"'.encode()
    with pytest.raises(PacketRefused):
        admit_packets({"a": payload}, max_request_bytes=10, max_input_units=4, max_calls=1)


def test_diagnostics_do_not_expose_request_text():
    result = admit_packets({"reviewer": b"private source"}, max_request_bytes=100,
                           max_input_units=100, max_calls=1)
    assert "private source" not in str(result)


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_invalid_budget_refused(limit):
    with pytest.raises(ValueError):
        admit_packets({"a": b"a"}, max_request_bytes=limit, max_input_units=100, max_calls=1)
