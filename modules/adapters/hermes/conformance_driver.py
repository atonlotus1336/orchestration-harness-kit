"""Map canonical conformance cases into the Hermes OS event shape."""

from __future__ import annotations


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "operation": operation,
        "payload": payload,
        "conversation": session_id,
        "state": state,
        "surface": "home",
    }
