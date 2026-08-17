"""Resource-aware admission that preserves native Hermes headroom."""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from prime_bridge.rollout_policy import AdmissionLimits


class AdmissionError(RuntimeError):
    pass


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ResourceSnapshot:
    status: str
    cpu_percent: float | None
    memory_percent: float | None
    sampled_at: str
    source: str

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> ResourceSnapshot:
        keys = {"status", "cpu_percent", "memory_percent", "sampled_at", "source"}
        if not isinstance(value, Mapping) or set(value) != keys:
            raise AdmissionError("resource snapshot is not closed")
        result = cls(**value)
        if result.status not in {"VERIFIED", "UNVERIFIED"} or not isinstance(result.source, str) or not result.source:
            raise AdmissionError("invalid resource snapshot status")
        _timestamp(result.sampled_at)
        if result.status == "VERIFIED":
            if any(type(item) not in {int, float} or not 0 <= float(item) <= 100 for item in (result.cpu_percent, result.memory_percent)):
                raise AdmissionError("invalid verified resource percentage")
        elif result.cpu_percent is not None or result.memory_percent is not None:
            raise AdmissionError("unverified resource values must be absent")
        return result


def _linux_sample() -> tuple[float, float]:
    def cpu() -> tuple[int, int]:
        values = [int(value) for value in open("/proc/stat", encoding="utf-8").readline().split()[1:]]  # noqa: SIM115
        return sum(values), values[3] + (values[4] if len(values) > 4 else 0)

    total1, idle1 = cpu()
    time.sleep(0.05)
    total2, idle2 = cpu()
    delta = max(total2 - total1, 1)
    cpu_percent = 100.0 * (1.0 - max(idle2 - idle1, 0) / delta)
    memory = {}
    for line in open("/proc/meminfo", encoding="utf-8"):  # noqa: SIM115
        key, raw = line.split(":", 1)
        memory[key] = int(raw.strip().split()[0])
    total = memory["MemTotal"]
    available = memory.get("MemAvailable", memory.get("MemFree", 0))
    return round(cpu_percent, 2), round(100.0 * (total - available) / total, 2)


def _windows_sample() -> tuple[float, float]:
    class Memory(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
            ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
            ("avail_extended", ctypes.c_ulonglong),
        ]

    def filetime() -> tuple[int, int, int]:
        idle, kernel, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError("GetSystemTimes failed")
        return idle.value, kernel.value, user.value

    first = filetime()
    time.sleep(0.05)
    second = filetime()
    idle = second[0] - first[0]
    total = (second[1] - first[1]) + (second[2] - first[2])
    cpu_percent = 100.0 * (1.0 - idle / max(total, 1))
    memory = Memory()
    memory.length = ctypes.sizeof(Memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError("GlobalMemoryStatusEx failed")
    return round(cpu_percent, 2), float(memory.memory_load)


def system_resource_snapshot() -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    try:
        cpu, memory = _windows_sample() if os.name == "nt" else _linux_sample()
    except (OSError, ValueError, KeyError, IndexError):
        return {"status": "UNVERIFIED", "cpu_percent": None, "memory_percent": None, "sampled_at": now, "source": "host-stdlib"}
    return {"status": "VERIFIED", "cpu_percent": cpu, "memory_percent": memory, "sampled_at": now, "source": "host-stdlib"}


class AdmissionController:
    def __init__(self, limits: AdmissionLimits, provider: Callable[[], Mapping[str, Any]] = system_resource_snapshot):
        self.limits = limits
        self.provider = provider
        self.last_decision: dict[str, Any] = {
            "status": "NOT_RUN", "reasons": [],
            "native_reserve_percent": limits.native_reserve_percent,
            "native_fallback": True,
        }

    def assess(self, job: Mapping[str, Any], active: list[Mapping[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
        reasons = []
        occupied = [item for item in active if item["status"] in {"starting", "running", "stopping"}]
        if len(occupied) >= self.limits.max_active_global:
            reasons.append("global-active-limit")
        if sum(item["profile"] == job["profile"] for item in occupied) >= self.limits.max_active_per_profile:
            reasons.append("profile-active-limit")
        try:
            sample = ResourceSnapshot.parse(self.provider())
        except (AdmissionError, TypeError, ValueError):
            sample = ResourceSnapshot("UNVERIFIED", None, None, datetime.now(timezone.utc).isoformat(), "invalid-provider")
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age = (instant - _timestamp(sample.sampled_at)).total_seconds()
        if sample.status != "VERIFIED":
            reasons.append("resource-state-unverified")
        elif age < -5 or age > self.limits.sample_max_age_seconds:
            reasons.append("resource-sample-stale")
        else:
            cpu_limit = min(self.limits.max_cpu_percent, 100 - self.limits.native_reserve_percent)
            memory_limit = min(self.limits.max_memory_percent, 100 - self.limits.native_reserve_percent)
            if float(sample.cpu_percent) > cpu_limit:
                reasons.append("cpu-pressure")
            if float(sample.memory_percent) > memory_limit:
                reasons.append("memory-pressure")
        self.last_decision = {
            "status": "ADMIT" if not reasons else "REJECT",
            "reasons": sorted(set(reasons)),
            "profile": job["profile"],
            "active_global": len(occupied),
            "active_profile": sum(item["profile"] == job["profile"] for item in occupied),
            "resource_status": sample.status,
            "cpu_percent": sample.cpu_percent,
            "memory_percent": sample.memory_percent,
            "native_reserve_percent": self.limits.native_reserve_percent,
            "native_fallback": True,
        }
        return self.last_decision
