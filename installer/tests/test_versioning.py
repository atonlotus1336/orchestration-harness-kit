import pytest

from installer.lib.versioning import IncompatibleProtocolError, negotiate_protocol


def test_negotiation_selects_highest_mutual_generation():
    assert negotiate_protocol([1, 2]) == 2


def test_negotiation_preserves_v1_peer_compatibility():
    assert negotiate_protocol([1]) == 1


def test_negotiation_ignores_unknown_future_generation_when_v2_is_mutual():
    assert negotiate_protocol([2, 3]) == 2


def test_negotiation_returns_structured_incompatibility_error():
    with pytest.raises(IncompatibleProtocolError) as caught:
        negotiate_protocol([3])
    assert caught.value.as_dict() == {
        "code": "INCOMPATIBLE_PROTOCOL",
        "message": "No mutually supported orchestration protocol version.",
        "local_supported": [1, 2],
        "remote_supported": [3],
    }


@pytest.mark.parametrize("versions", [[], [0], [True], ["2"]])
def test_negotiation_rejects_invalid_version_sets(versions):
    with pytest.raises(ValueError):
        negotiate_protocol(versions)
