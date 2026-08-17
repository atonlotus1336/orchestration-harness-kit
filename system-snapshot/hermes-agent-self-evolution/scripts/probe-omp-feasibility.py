#!/usr/bin/env python3
"""Phase 10 Plan 01 feasibility probe.

Fail-closed gate for Prime-governed OMP coding. Emits exactly one typed verdict:
READY, PHASE9_BLOCKED, RUNTIME_UNAVAILABLE, MODEL_INCOMPATIBLE, or OMP_RUNTIME_UNSAFE.

Contract (CONTEXT.md small-model rules):
  - Only READY authorizes Plan 02.
  - Missing Phase 9 live evidence => PHASE9_BLOCKED.
  - Missing/unverifiable runtime => RUNTIME_UNAVAILABLE.
  - Model route incompatible => MODEL_INCOMPATIBLE.
  - Any uncontrolled SDK boundary => OMP_RUNTIME_UNSAFE.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA_PATH = ROOT / "config" / "omp-feasibility.schema.json"
LOCK_PATH = ROOT / "config" / "omp-runtime-lock.json"
PHASE9_SUMMARY = ROOT / ".planning" / "phases" / "09-automatic-prime-readiness-gsd-routing" / "09-04-SUMMARY.md"
PHASE9_EVIDENCE_DIR = ROOT / ".planning" / "phases" / "09-automatic-prime-readiness-gsd-routing" / "evidence"
EVIDENCE_OUT = ROOT / ".planning" / "phases" / "10-prime-governed-omp-coding" / "evidence" / "omp-feasibility.json"
SDK_PROBE = ROOT / "scripts" / "omp" / "probe-controlled-sdk.mjs"

PINNED_PACKAGE = "@oh-my-pi/pi-coding-agent"
PINNED_VERSION = "17.3.0"
PINNED_INTEGRITY = "sha512-Ad2VB+dRxmKACarbq7eM9/cjfWlZWeKBliqAP5OpWU8vrj8i4xLVtuJpTJ3q3Ile9Tau2YbzBSJeWHDNAMtIhw=="
BUN_MINIMUM = "1.3.14"

# Canonical Phase 9 live route receipt markers (from 09-04-SUMMARY.md / 09-04-PLAN.md).
PHASE9_RECEIPT_MARKERS = (
    "route receipt",
    "route_receipt",
    "PHASE9",
    "live route",
    "restart-boundary rehearsal",
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bun_version() -> str | None:
    try:
        result = subprocess.run(
            ["bun", "--version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _version_satisfies(version: str | None, minimum: str) -> bool:
    if not version:
        return False
    try:
        parts = [int(part) for part in version.split(".")]
        minimum_parts = [int(part) for part in minimum.split(".")]
    except ValueError:
        return False
    return parts >= minimum_parts


def _phase9_receipt() -> dict:
    """Return the canonical Phase 9 live route receipt check (fail-closed).

    A live route receipt is a fresh evidence artifact (a JSON file under the
    Phase 9 evidence directory), never the plan/summary text. The summary
    explicitly defers the live checkpoint, so its prose must not be treated as
    evidence.
    """
    receipt_path = ""
    present = False
    if PHASE9_EVIDENCE_DIR.is_dir():
        candidates = sorted(PHASE9_EVIDENCE_DIR.glob("*.json"))
        for candidate in candidates:
            try:
                raw = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if any(marker in raw for marker in PHASE9_RECEIPT_MARKERS):
                receipt_path = str(candidate.relative_to(ROOT))
                present = True
                break
    return {
        "status": "PASS" if present else "BLOCKED",
        "route_receipt_present": present,
        "native_fallback_receipt_present": present,
        "receipt_path": receipt_path,
    }


def _runtime_lock() -> dict:
    """Verify the immutable runtime lock against the pinned package and Bun."""
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "status": "FAIL",
            "package": {"name": PINNED_PACKAGE, "version": PINNED_VERSION, "integrity": PINNED_INTEGRITY, "integrity_matches": False},
            "bun": {"version": _bun_version() or "", "minimum": BUN_MINIMUM, "satisfies": False},
            "image_digest": "UNRESOLVED", "license": "UNRESOLVED", "sbom_sha256": "UNRESOLVED", "source_commit": "UNRESOLVED",
        }
    package = lock.get("package", {})
    integrity_matches = (
        package.get("name") == PINNED_PACKAGE
        and package.get("version") == PINNED_VERSION
        and package.get("integrity") == PINNED_INTEGRITY
    )
    bun = _bun_version()
    satisfies = _version_satisfies(bun, BUN_MINIMUM)
    source = lock.get("source", {})
    image = lock.get("image", {})
    resolved = (
        integrity_matches
        and satisfies
        and source.get("commit") not in (None, "", "UNRESOLVED")
        and source.get("license") not in (None, "", "UNRESOLVED")
        and image.get("digest") not in (None, "", "UNRESOLVED")
        and image.get("sbom_sha256") not in (None, "", "UNRESOLVED")
    )
    return {
        "status": "PASS" if resolved else "FAIL",
        "package": {
            "name": package.get("name", ""), "version": package.get("version", ""),
            "integrity": package.get("integrity", ""), "integrity_matches": integrity_matches,
        },
        "bun": {"version": bun or "", "minimum": BUN_MINIMUM, "satisfies": satisfies},
        "image_digest": image.get("digest", "UNRESOLVED"),
        "license": source.get("license", "UNRESOLVED"),
        "sbom_sha256": image.get("sbom_sha256", "UNRESOLVED"),
        "source_commit": source.get("commit", "UNRESOLVED"),
    }


def _run_sdk_probe() -> dict:
    """Run the controlled live-model SDK probe inside the pinned sidecar container.

    The OMP SDK boundary lives inside the sibling rootless container, never on
    the host. The sidecar reaches the model only through the Hermes inference
    relay (pinned to 127.0.0.1:11434). Returns a fail-closed result.
    """
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        image = lock.get("image", {}).get("tag") or "localhost/hermes-omp-sidecar:phase10-a"
    except (OSError, ValueError):
        image = "localhost/hermes-omp-sidecar:phase10-a"

    relay_socket = Path(tempfile.mkdtemp(prefix="omp-relay-")) / "relay.sock"
    relay = None
    relay_loop = None
    relay_thread = None
    try:
        from prime_bridge.inference_relay import InferenceRelay, RelayPolicy
        relay = InferenceRelay(relay_socket, RelayPolicy("127.0.0.1", ("127.0.0.1",), 11434))
        relay_loop = asyncio.new_event_loop()
        relay_loop.run_until_complete(relay.start())
        relay_thread = threading.Thread(target=relay_loop.run_forever, daemon=True)
        relay_thread.start()
    except Exception as exc:  # noqa: BLE001 - fail closed on relay startup
        return {
            "status": "FAIL", "ran": False, "tool_inventory": [], "discovery_sources": [],
            "assertions": {
                "streaming": False, "tool_calls": False, "cancellation": False, "usage": False,
                "lsp_read": False, "apply_edit_rejected": False, "no_alternate_model": False,
                "no_persisted_files": False, "descendant_cleanup": False,
            },
            "probe_error": f"relay_start_failed: {exc}",
        }
    try:
        result = subprocess.run(
            [
                "podman", "run", "--rm", "--network", "none", "--read-only",
                "--tmpfs", "/tmp",
                "--mount", f"type=bind,src={relay_socket},target=/run/prime-relay/relay.sock,ro=true",
                "--mount", f"type=bind,src={ROOT / 'scripts/omp/relay-client.mjs'},target=/opt/omp/relay-client.mjs,ro=true",
                "--mount", f"type=bind,src={ROOT / 'scripts/omp/live-gate-sidecar.mjs'},target=/opt/omp/live-gate-sidecar.mjs,ro=true",
                "--entrypoint", "sh", image, "-c",
                "bun /opt/omp/relay-client.mjs & sleep 1; bun /opt/omp/live-gate-sidecar.mjs",
            ],
            capture_output=True, text=True, timeout=180, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "FAIL", "ran": False, "tool_inventory": [], "discovery_sources": [],
            "assertions": {
                "streaming": False, "tool_calls": False, "cancellation": False, "usage": False,
                "lsp_read": False, "apply_edit_rejected": False, "no_alternate_model": False,
                "no_persisted_files": False, "descendant_cleanup": False,
            },
            "probe_error": str(exc),
        }
    finally:
        if relay is not None and relay_loop is not None:
            try:
                close_future = asyncio.run_coroutine_threadsafe(relay.close(), relay_loop)
                close_future.result(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    relay_loop.call_soon_threadsafe(relay_loop.stop)
                    if relay_thread is not None:
                        relay_thread.join(timeout=5)
                    relay_loop.close()
                except Exception:  # noqa: BLE001
                    pass
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "status": "FAIL", "ran": True, "tool_inventory": [], "discovery_sources": [],
            "assertions": {
                "streaming": False, "tool_calls": False, "cancellation": False, "usage": False,
                "lsp_read": False, "apply_edit_rejected": False, "no_alternate_model": False,
                "no_persisted_files": False, "descendant_cleanup": False,
            },
            "probe_error": f"non-JSON stdout: {result.stdout[:200]!r}",
        }
    assertions = value.get("assertions", {})
    all_pass = (
        result.returncode == 0
        and assertions.get("streaming") is True
        and assertions.get("tool_calls") is True
        and assertions.get("cancellation") is True
        and assertions.get("usage") is True
        and assertions.get("lsp_read") is True
        and assertions.get("apply_edit_rejected") is True
        and assertions.get("no_alternate_model") is True
        and assertions.get("no_persisted_files") is True
        and assertions.get("descendant_cleanup") is True
    )
    return {
        "status": "PASS" if all_pass else "FAIL",
        "ran": True,
        "tool_inventory": value.get("tool_inventory", []),
        "discovery_sources": value.get("discovery_sources", []),
        "assertions": {
            "streaming": assertions.get("streaming", False),
            "tool_calls": assertions.get("tool_calls", False),
            "cancellation": assertions.get("cancellation", False),
            "usage": assertions.get("usage", False),
            "lsp_read": assertions.get("lsp_read", False),
            "apply_edit_rejected": assertions.get("apply_edit_rejected", False),
            "no_alternate_model": assertions.get("no_alternate_model", False),
            "no_persisted_files": assertions.get("no_persisted_files", False),
            "descendant_cleanup": assertions.get("descendant_cleanup", False),
        },
    }


def _verdict(phase9: dict, runtime: dict, sdk: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if phase9["status"] != "PASS":
        reasons.append("phase9_live_route_receipt_missing")
        return "PHASE9_BLOCKED", reasons
    if runtime["status"] != "PASS":
        reasons.append("runtime_lock_unresolved")
        return "RUNTIME_UNAVAILABLE", reasons
    if sdk["status"] != "PASS":
        if sdk.get("ran") is False:
            reasons.append("sdk_probe_not_run")
            return "RUNTIME_UNAVAILABLE", reasons
        reasons.append("sdk_boundary_uncontrolled")
        return "OMP_RUNTIME_UNSAFE", reasons
    reasons.append("all_checks_pass")
    return "READY", reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="OMP feasibility probe")
    parser.add_argument("--strict", action="store_true", help="fail closed on any non-READY verdict")
    parser.add_argument("--write-evidence", action="store_true", help="write the evidence JSON")
    args = parser.parse_args()

    phase9 = _phase9_receipt()
    runtime = _runtime_lock()
    sdk = _run_sdk_probe()
    verdict, reasons = _verdict(phase9, runtime, sdk)

    report = {
        "schema_version": "1.0",
        "verdict": verdict,
        "captured_at": _now(),
        "phase9": phase9,
        "runtime_lock": runtime,
        "sdk_probe": sdk,
        "reasons": reasons,
    }
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)

    if args.write_evidence:
        EVIDENCE_OUT.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_OUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, sort_keys=True))
    if args.strict and verdict != "READY":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
