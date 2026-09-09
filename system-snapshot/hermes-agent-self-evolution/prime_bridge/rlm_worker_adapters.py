"""Production RLM workers over the existing governed Prime capability broker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Mapping


class PrimeRlmAdapterError(RuntimeError):
    pass


class PrimeRlmWorkerAdapters:
    """Run coding leaves through OMP and read-only leaves through Hermes MCP.

    OMP remains single-flight by design.  The lock converts RLM fan-out into a
    reliable bounded queue instead of adding a competing sidecar topology.
    """

    def __init__(self, *, broker: Any, omp_service: Any, ledger: Any, job_id: str, profile: str) -> None:
        self.broker = broker
        self.omp_service = omp_service
        self.ledger = ledger
        self.job_id = job_id
        self.profile = profile
        self._omp_lock = asyncio.Lock()
        self.proposals: dict[str, Mapping[str, Any]] = {}

    async def __call__(self, item: Mapping[str, Any], binding: Mapping[str, Any]) -> Mapping[str, Any]:
        return await (self.omp(item, binding) if item.get("kind") == "coding" else self.read_only(item, binding))

    @staticmethod
    def _usage(value: Mapping[str, Any], *, wall_seconds: float, model_calls: int) -> dict[str, Any]:
        sdk = value.get("sdk_evidence") if isinstance(value.get("sdk_evidence"), Mapping) else {}
        raw = sdk.get("usage") if isinstance(sdk.get("usage"), Mapping) else {}
        tokens = raw.get("total", 0)
        return {
            "model_calls": model_calls,
            "tool_calls": 1,
            "wall_seconds": max(0, min(round(wall_seconds, 3), 2_147_483_647)),
            "tokens": int(tokens) if type(tokens) is int and tokens >= 0 else 0,
        }

    async def omp(self, item: Mapping[str, Any], _binding: Mapping[str, Any]) -> Mapping[str, Any]:
        worker_input = item.get("worker_input") if isinstance(item.get("worker_input"), Mapping) else {}
        packet = worker_input.get("task_packet")
        if not isinstance(packet, Mapping) or not isinstance(packet.get("packet_hash"), str):
            raise PrimeRlmAdapterError("RLM OMP unit requires a closed task packet")
        allowed_paths = packet.get("allowed_paths")
        if not isinstance(allowed_paths, list) or not allowed_paths:
            raise PrimeRlmAdapterError("RLM OMP task packet has no bounded paths")
        started = time.monotonic()
        async with self._omp_lock:
            self.ledger.note(self.job_id, "omp_task_packet", {
                "packet_hash": packet["packet_hash"],
                "allowed_paths": list(allowed_paths),
                "edit_class": packet.get("edit_class"),
                "risk_tier": packet.get("risk_tier"),
                "rlm_item_id": item["item_id"],
            })
            response = await self.broker.handle({
                "job_id": self.job_id, "profile": self.profile,
                "alias": "omp.worker.run",
                "arguments": {"prompt": (
                    "Execute this closed RLM-owned OMP leaf. "
                    f"packet_hash={packet['packet_hash']}\n"
                    + json.dumps(packet, sort_keys=True, separators=(",", ":"))
                )},
            })
            if response.get("status") != "ok":
                raise PrimeRlmAdapterError("governed OMP adapter did not complete")
            result = response.get("result") if isinstance(response.get("result"), Mapping) else {}
            child_id = str(result.get("child_omp_job_id") or "")
            proposal = self.omp_service.result_for(child_id)
            if not isinstance(proposal, Mapping):
                raise PrimeRlmAdapterError("governed OMP proposal is unavailable")
            self.proposals[str(item["item_id"])] = proposal
        patch = proposal.get("patch") if isinstance(proposal.get("patch"), Mapping) else {}
        digest = str(patch.get("patch_hash") or "")
        return {
            "summary": f"OMP patch proposal sha256:{digest}",
            "artifact": {
                "patch_hash": digest, "changed_paths": list(patch.get("changed_paths") or ()),
                "child_omp_job_id": child_id,
            },
            "usage": self._usage(proposal, wall_seconds=time.monotonic() - started, model_calls=1),
        }

    async def read_only(self, item: Mapping[str, Any], _binding: Mapping[str, Any]) -> Mapping[str, Any]:
        tools = list(item.get("allowed_tools") or ())
        worker_input = item.get("worker_input") if isinstance(item.get("worker_input"), Mapping) else {}
        arguments = worker_input.get("arguments") or {}
        if len(tools) != 1 or not isinstance(arguments, Mapping):
            raise PrimeRlmAdapterError("read-only RLM unit requires one exact capability call")
        started = time.monotonic()
        response = await self.broker.handle({
            "job_id": self.job_id, "profile": self.profile,
            "alias": tools[0], "arguments": dict(arguments),
        })
        if response.get("status") != "ok":
            raise PrimeRlmAdapterError("governed read-only adapter did not complete")
        result = response.get("result")
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str)
        return {
            "summary": encoded[:4096],
            "artifact": {"result_digest": hashlib.sha256(encoded.encode()).hexdigest()},
            "usage": self._usage({}, wall_seconds=time.monotonic() - started, model_calls=0),
        }


__all__ = ["PrimeRlmAdapterError", "PrimeRlmWorkerAdapters"]
