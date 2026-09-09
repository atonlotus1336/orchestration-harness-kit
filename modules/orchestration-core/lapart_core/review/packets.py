"""All-or-nothing admission of complete requests; no truncation or model calls."""
import hashlib
import json
from collections.abc import Mapping


class PacketRefused(ValueError):
    def __init__(self, reason, report):
        super().__init__(reason)
        self.report = {**report, "admitted": False, "reason": reason}


def finalize_packets(packets, limits):
    """Reserve fixed output for the complete set before hashing or dispatch."""
    count = len(packets)
    if not count:
        raise ValueError("complete reviewer set required")
    cap = min(2048, limits['max_output_tokens'] // count,
              (limits['max_total_tokens'] - sum(map(len, packets.values()))) // count)
    if cap < 1:
        raise ValueError("complete reviewer set exceeds output or total budget")
    finalized = {}
    for key, payload in packets.items():
        value = json.loads(payload)
        value['max_tokens'] = cap
        finalized[key] = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return finalized


def admit_packets(packets: Mapping[str, bytes], *, max_request_bytes: int,
                  max_input_units: int, max_calls: int) -> dict:
    if any(type(v) is not int or v < 0 for v in (max_request_bytes, max_input_units, max_calls)):
        raise ValueError("invalid packet budget")
    if (not isinstance(packets, Mapping) or not packets or any(
            not isinstance(k, str) or not k or type(v) is not bytes or not v
            for k, v in packets.items())):
        raise ValueError("complete nonempty serialized packets required")
    sizes = {k: len(v) for k, v in sorted(packets.items())}
    report = {"schema_version": "review-packets.v1", "admitted": True,
              "request_count": len(packets), "request_bytes": sizes,
              "estimated_input_units": sum(sizes.values()),
              "input_unit_basis": "utf8_request_bytes_conservative",
              "packet_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(packets.items())},
              "limits": {"request_bytes": max_request_bytes,
                         "aggregate_input_units": max_input_units, "calls": max_calls}}
    if len(packets) > max_calls:
        raise PacketRefused("complete reviewer set exceeds model-call budget", report)
    if any(size > max_request_bytes for size in sizes.values()):
        raise PacketRefused("complete reviewer packet exceeds byte budget", report)
    if sum(sizes.values()) > max_input_units:
        raise PacketRefused("complete reviewer set exceeds aggregate estimated input budget", report)
    return report
