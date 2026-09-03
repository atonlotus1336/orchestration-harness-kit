"""Maps canonical conformance cases into the fake harness's local event shape."""

from __future__ import annotations


def make_event(operation: str, payload: dict, session_id: str, state: str) -> dict:
    return {
        "op": operation,
        "body": payload,
        "conversation": session_id,
        "state": state,
    }
