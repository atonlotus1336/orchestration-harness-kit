#!/usr/bin/env python3
"""Run a real serial-native versus parallel-Orca coding qualification."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

from prime_bridge.adaptive_production_authorization import host_key
from prime_bridge.job_cli import load_runtime_authority
from prime_bridge.job_ledger import JobLedger
from prime_bridge.mcp_server import ROOT, _profile_route
from prime_bridge.orca_execution import (
    HarnessAdapter, OrcaHarnessRegistry, OrcaExecutionProvider, OrcaCLI,
    discover_orca_executable,
)
from prime_bridge.orchestration_switch import default_state_path
from prime_bridge.worker_transport import WorkerTransport, normalize_orca_worker_show


def _run(command, *, cwd: Path, timeout: int = 300):
    started = time.monotonic()
    result = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, timeout=timeout,
    )
    return result, time.monotonic() - started


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _build_repo(path: Path) -> None:
    path.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (path / f"calc_{name}.py").write_text(
            f'def {name}(left, right):\n    """Return the sum of two numbers."""\n'
            '    raise NotImplementedError\n', encoding="utf-8",
        )
    (path / "test_calc.py").write_text(
        "import unittest\n"
        "from calc_alpha import alpha\nfrom calc_beta import beta\n\n"
        "class CalculatorTest(unittest.TestCase):\n"
        "    def test_alpha(self): self.assertEqual(alpha(2, 3), 5)\n"
        "    def test_beta(self): self.assertEqual(beta(-2, 5), 3)\n\n"
        "if __name__ == '__main__': unittest.main()\n", encoding="utf-8",
    )
    _git(path, "init")
    _git(path, "config", "user.name", "Orca Qualification")
    _git(path, "config", "user.email", "orca-qualification@example.invalid")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "qualification fixture")


def _codex_executable() -> str:
    override = os.environ.get("LAPART_CODEX_CLI")
    if override and (Path(override).is_file() or shutil.which(override)):
        return override
    native = shutil.which("codex")
    if native:
        probe = subprocess.run([native, "--version"], capture_output=True, timeout=10)
        if probe.returncode == 0:
            return native
    users = Path("/mnt/c/Users")
    candidates = sorted(
        item for item in users.glob("*/AppData/Local/OpenAI/Codex/bin/*/codex.exe")
        if item.is_file()
    ) if users.is_dir() else []
    if len(candidates) != 1:
        raise RuntimeError("exactly one working Codex CLI is required")
    return str(candidates[0])


def _codex_workspace(executable: str, path: Path) -> str:
    if executable.lower().endswith(".exe") and str(path).startswith("/mnt/"):
        result = subprocess.run(
            ["wslpath", "-w", str(path)], text=True, capture_output=True, check=True,
        )
        return result.stdout.strip()
    return str(path)


def _prompt(name: str) -> str:
    targeted = (
        f'python -c "from calc_{name} import {name}; '
        f'assert {name}(2, 3) == 5; assert {name}(-2, 5) == 3"'
    )
    return (
        f"Implement only calc_{name}.py so {name}(left, right) returns their sum. "
        f"Do not edit any other file. Run this isolated check and finish only after it passes: {targeted}"
    )


def _target_check(name: str) -> list[str]:
    expression = (
        f"from calc_{name} import {name}; "
        f"assert {name}(2, 3) == 5; assert {name}(-2, 5) == 3"
    )
    return [sys.executable, "-c", expression]


def _protocol_sha256() -> str:
    material = {name: _prompt(name) for name in ("alpha", "beta")}
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _remove_test_caches(root: Path) -> None:
    for cache in root.rglob("__pycache__"):
        if cache.is_dir() and cache.resolve().is_relative_to(root.resolve()):
            shutil.rmtree(cache)


def _changed(repo: Path) -> list[str]:
    tracked = _git(repo, "diff", "--name-only", "HEAD", "--").splitlines()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted(set(filter(None, [*tracked, *untracked])))


def _runtime() -> dict[str, str]:
    source_commit = _git(ROOT, "rev-parse", "HEAD")
    executable = Path(discover_orca_executable())
    material = executable.read_bytes() if executable.is_file() else b"orca-unavailable"
    value = {
        "source_commit": source_commit,
        "image_digest": "sha256:" + hashlib.sha256(material).hexdigest(),
        "sandbox": "rootless-podman",
        "command_fingerprint": hashlib.sha256(b"orca-codex-ab-v1").hexdigest(),
    }
    value["fingerprint"] = JobLedger.runtime_fingerprint(value)
    return value


async def _orca_arm(*, source: Path, integration: Path, run_root: Path,
                    ledger: JobLedger, job: dict, model: str, timeout: int,
                    codex_launch: str):
    transport = WorkerTransport(ledger, orca_harnesses=("codex",))
    registry = OrcaHarnessRegistry.load(ROOT / "config/orca-harness-adapters.json")
    if codex_launch == "terminal":
        registry.adapters["codex"] = HarnessAdapter(
            "codex", "terminal", None, ("codex",), "configured",
        )
    provider = OrcaExecutionProvider(
        ledger=ledger, transport=transport, registry=registry,

        client=OrcaCLI(cwd=ROOT), switch_path=default_state_path(),
    )
    ledger.note(job["job_id"], "adaptive_provider_selected", {
        "provider": "orca", "harness": "codex", "actual_lane": "T3_PRIME_OMP",
        "admitted_count": 2, "qualification_reference": "ab-candidate",
    })
    worktrees = run_root / "orca-worktrees"
    worktrees.mkdir(parents=True, exist_ok=True)
    receipts = []
    contexts = []
    for name in ("alpha", "beta"):
        packet_hash = hashlib.sha256(_prompt(name).encode()).hexdigest()
        receipt = transport.prepare(
            job_id=job["job_id"], task_id=f"ab-{name}", goal_id=None,
            contract_sha256=packet_hash, harness="codex",
            declared_paths=[f"calc_{name}.py"], worktree_root=worktrees,
            allow_parallel=True, allocator=provider.allocate_worktree,
        )["receipt"]
        capability = hashlib.sha256(f"{job['job_id']}:{name}".encode()).hexdigest()
        request_id = uuid.uuid4().hex
        ledger.note(job["job_id"], "capability_authorized", {
            "request_id": request_id, "alias": "orca.worker.start",
            "capability_digest": hashlib.sha256(capability.encode()).hexdigest(),
        })
        receipts.append((name, receipt))
        contexts.append({
            "job_id": job["job_id"], "profile": job["profile"], "harness": "codex",
            "request_id": request_id, "capability_receipt": capability,
        })
    started = time.monotonic()
    records = await asyncio.gather(*(
        asyncio.to_thread(
            provider.start, binding_id=receipt["binding_id"], objective=_prompt(name),
            task_spec=_prompt(name), context=context, model=model, effort="medium",
        )
        for (name, receipt), context in zip(receipts, contexts)
    ))
    pending = {record["binding_id"] for record in records}
    deadline = time.monotonic() + timeout
    statuses = {}
    while pending and time.monotonic() < deadline:
        for binding_id in tuple(pending):
            shown = await asyncio.to_thread(provider.show, binding_id)
            _dispatch, _worker, status = normalize_orca_worker_show(shown["worker_envelope"])
            statuses[binding_id] = status
            if status == "done":
                pending.remove(binding_id)
            elif status in {"failed", "cancelled"}:
                pending.remove(binding_id)
        if pending:
            await asyncio.sleep(1)
    elapsed = time.monotonic() - started
    if pending or any(status != "done" for status in statuses.values()):
        for binding_id in pending:
            try:
                await asyncio.to_thread(provider.stop, binding_id)
            except Exception:
                pass
        raise RuntimeError("Orca workers did not complete successfully")
    shutil.copytree(source, integration, ignore=shutil.ignore_patterns(".git"))
    changed = {}
    for name, receipt in receipts:
        worktree = Path(receipt["worktree"])
        _remove_test_caches(worktree)
        observed = transport.collect(receipt["binding_id"])["files"]
        expected = f"calc_{name}.py"
        if set(observed) != {expected}:
            raise RuntimeError(f"Orca worker path violation for {name}: {sorted(observed)}")
        shutil.copy2(worktree / expected, integration / expected)
        changed[name] = observed
        await asyncio.to_thread(provider.release, receipt["binding_id"])
    tests, test_seconds = await asyncio.to_thread(
        _run, [sys.executable, "-m", "unittest", "-q"], cwd=integration, timeout=60,
    )
    return {
        "seconds": elapsed, "test_seconds": test_seconds,
        "test_returncode": tests.returncode, "changed": changed,
        "bindings": [receipt["binding_id"] for _name, receipt in receipts],
        "dispatches": [record["dispatch_id"] for record in records],
        "successes": 2 if tests.returncode == 0 else 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--profile", default="workspace")
    parser.add_argument("--profile-config", type=Path,
                        default=Path.home() / ".hermes/profiles/workspace/config.yaml")
    parser.add_argument("--ledger", type=Path,
                        default=Path(os.environ.get("HERMES_PRIME_LEDGER", Path.home() / ".hermes/state/prime/jobs.sqlite3")))
    parser.add_argument("--state-root", type=Path, default=ROOT / ".state/orca-coding")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--promote-registry", action="store_true")
    parser.add_argument("--codex-launch", choices=("native", "terminal"), default="native")
    parser.add_argument("--native-baseline", type=Path)
    args = parser.parse_args(argv)
    key = host_key()
    if not key:
        raise SystemExit("qualification signing authority is unavailable")
    now = datetime.now(timezone.utc)
    run_root = args.state_root / ("ab-" + now.strftime("%Y%m%dT%H%M%SZ"))
    base = run_root / "base"
    native = run_root / "native"
    orca_source = run_root / "orca-source"
    integration = run_root / "orca-integrated"
    _build_repo(base)
    shutil.copytree(base, native)
    shutil.copytree(base, orca_source)
    route, _ = _profile_route(args.profile, args.profile_config)
    baseline_reference = None
    native_repo = native
    if args.native_baseline:
        baseline_bytes = args.native_baseline.read_bytes()
        baseline = json.loads(baseline_bytes)
        if (baseline.get("kind") != "real-coding-ab"
                or baseline.get("model") != args.model
                or baseline.get("profile") != args.profile
                or baseline.get("route_sha256") != route["fingerprint"]
                or baseline.get("protocol_sha256") != _protocol_sha256()
                or len(baseline.get("native", {}).get("runs", [])) != 2
                or any(item.get("returncode") != 0
                       or item.get("target_test_returncode") != 0
                       for item in baseline["native"]["runs"])):
            raise SystemExit("native baseline evidence is incompatible")
        native_repo = args.native_baseline.parent / "native"
        _remove_test_caches(native_repo)
        native_tests, native_test_seconds = _run(
            [sys.executable, "-m", "unittest", "-q"], cwd=native_repo, timeout=60,
        )
        native_runs = baseline["native"]["runs"]
        native_seconds = float(baseline["native"]["seconds"])
        baseline_reference = hashlib.sha256(baseline_bytes).hexdigest()
    else:
        native_started = time.monotonic()
        codex_executable = _codex_executable()
        native_runs = []
        for name in ("alpha", "beta"):
            result, elapsed = _run([
                codex_executable, "exec", "-m", args.model, "--approve-for-me",
                "--ephemeral", "--ignore-rules",
                "-C", _codex_workspace(codex_executable, native), _prompt(name),
            ], cwd=native, timeout=args.timeout)
            targeted, targeted_seconds = _run(
                _target_check(name), cwd=native, timeout=60,
            )
            native_runs.append({
                "name": name, "returncode": result.returncode, "seconds": elapsed,
                "target_test_returncode": targeted.returncode,
                "target_test_seconds": targeted_seconds,
                "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
            })
        native_seconds = time.monotonic() - native_started
        native_tests, native_test_seconds = _run(
            [sys.executable, "-m", "unittest", "-q"], cwd=native, timeout=60,
        )
    ledger = JobLedger(args.ledger, [run_root])
    job = ledger.create(
        owner="user", profile=args.profile, project="orca-coding-ab",
        session_id="orca-ab-" + uuid.uuid4().hex[:12], workspace=orca_source,
        route=route, runtime=load_runtime_authority(ROOT / "config/prime-runtime-authority.json"),
        limits={"max_iterations": 4, "no_progress_seconds": 120,
                "wall_seconds": args.timeout},
    )
    failure = None
    failure_trace = None
    try:
        orca = asyncio.run(_orca_arm(
            source=orca_source, integration=integration, run_root=run_root,
            ledger=ledger, job=job, model=args.model, timeout=args.timeout,
            codex_launch=args.codex_launch,
        ))
    except Exception as exc:
        failure = type(exc).__name__ + ": " + str(exc)
        failure_trace = traceback.format_exc().splitlines()[-12:]
        orca = {"seconds": None, "test_returncode": None, "successes": 0}
    _remove_test_caches(native_repo)
    native_successes = sum(
        item["returncode"] == 0 and item.get("target_test_returncode") == 0
        for item in native_runs
    )
    expected_native = {"calc_alpha.py", "calc_beta.py"}
    native_paths_ok = set(_changed(native_repo)) == expected_native
    ratio = (native_seconds / orca["seconds"]) if orca.get("seconds") else 0.0
    qualified = (
        failure is None and native_successes == 2 and native_tests.returncode == 0
        and native_paths_ok and orca["successes"] == 2
        and orca["test_returncode"] == 0 and ratio >= 1.0
    )
    evidence = {
        "schema_version": "1.0", "kind": "real-coding-ab",
        "harness": "codex", "model": args.model, "profile": args.profile,
        "route_sha256": route["fingerprint"],
        "protocol_sha256": _protocol_sha256(), "job_id": job["job_id"],
        "ledger": str(args.ledger), "started_at": now.isoformat().replace("+00:00", "Z"),
        "native": {"seconds": native_seconds, "test_seconds": native_test_seconds,
                   "baseline_evidence_sha256": baseline_reference,
                   "test_returncode": native_tests.returncode,
                   "paths_ok": native_paths_ok, "runs": native_runs},
        "orca": orca, "wall_time_ratio": ratio,
        "qualified": qualified, "failure": failure, "failure_trace": failure_trace,
    }
    evidence_path = run_root / "evidence.json"
    _atomic(evidence_path, evidence)
    evidence_sha = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    ledger.note(job["job_id"], "orca_ab_qualification", {
        "qualified": qualified, "evidence_sha256": evidence_sha,
        "wall_time_ratio": ratio, "harness": "codex",
    })
    if not qualified:
        print(json.dumps({"status": "NOT_QUALIFIED", "evidence": str(evidence_path),
                          "failure": failure, "wall_time_ratio": ratio}, indent=2))
        return 2
    record = {
        "status": "qualified", "harness": "codex",
        "route_sha256": route["fingerprint"],
        "qualified_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        "sample_count": 4, "quality_regressions": 0,
        "wall_time_ratio": ratio, "native_successes": native_successes,
        "orca_successes": orca["successes"], "evidence_sha256": evidence_sha,
        "automatic_dispatch_authorized": True,
        "max_parallel": args.max_parallel,
        "adapter_launch": args.codex_launch,
    }
    qualification = {"schema_version": "1.0", "provider": "orca",
                     "records": {"codex": record}}
    qualification["authority_hmac_sha256"] = hmac.new(
        key, json.dumps(qualification, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    qualification_path = args.state_root / "qualification.json"
    _atomic(qualification_path, qualification)
    if args.promote_registry:
        registry_path = ROOT / "config/orca-harness-adapters.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["adapters"]["codex"]["qualification"] = "production"
        registry["adapters"]["codex"]["launch"] = args.codex_launch
        registry["adapters"]["codex"]["agent"] = (
            "codex" if args.codex_launch == "native" else None
        )
        registry["adapters"]["codex"]["command"] = (
            [] if args.codex_launch == "native" else ["codex"]
        )
        _atomic(registry_path, registry)
    print(json.dumps({"status": "QUALIFIED", "evidence": str(evidence_path),
                      "qualification": str(qualification_path),
                      "wall_time_ratio": ratio, "job_id": job["job_id"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
