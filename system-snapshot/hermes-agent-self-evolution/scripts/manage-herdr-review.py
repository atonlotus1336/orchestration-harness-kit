#!/usr/bin/env python3
"""Transactional operator surface for the persistent Herdr evaluator runtime."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONFIG = ROOT / "config/herdr-evaluator-adapters.json"
LOCK = ROOT / "config/herdr-runtime-lock.json"
PROBE = ROOT / "scripts/probe-herdr-evaluator.py"
BRIDGE = ROOT / "scripts/herdr-codex-reviewer.py"
CATALOG = ROOT / "config/adaptive-reviewer-catalog.json"
STATE = ROOT / ".state/herdr-review"
SNAPSHOTS = STATE / "snapshots"
RECEIPTS = STATE / "receipts"
MODES = {"off", "observe", "supervised", "full"}
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def runtime_config_path(target: str = "windows") -> Path:
    if target not in {"windows", "wsl"}:
        raise ValueError("unknown Herdr runtime target")
    suffix = "" if target == "windows" else "-wsl"
    return STATE / f"runtime-config{suffix}.json"


def runtime_lock_path(target: str = "windows") -> Path:
    if target not in {"windows", "wsl"}:
        raise ValueError("unknown Herdr runtime target")
    suffix = "" if target == "windows" else "-wsl"
    return STATE / f"runtime-lock{suffix}.json"


def read_config(target: str = "windows") -> dict:
    path = runtime_config_path(target)
    return read(path if path.is_file() else CONFIG)


def read_lock(target: str = "windows") -> dict:
    path = runtime_lock_path(target)
    return read(path if path.is_file() else LOCK)


class OperationLock:
    """Serialize lifecycle mutations so config, lock, and gates move as one unit."""

    def __init__(self, timeout: float = 10.0):
        self.path = STATE / ".activation.lock"
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Herdr lifecycle operation is already in progress")
                time.sleep(0.05)

    def __exit__(self, *_args):
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".herdr-review-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore(path: Path, value) -> None:
    if value is None:
        path.unlink(missing_ok=True)
    else:
        atomic(path, value)


def probe(
    executable: Path | None = None, *, target: str = "windows",
    lock_path: Path | None = None,
):
    spec = importlib.util.spec_from_file_location("herdr_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = lock_path or runtime_lock_path(target)
    return module.probe(path if path.is_file() else LOCK, executable)


def switch_owner():
    from prime_bridge.orchestration_switch import OrchestrationSwitch, default_state_path
    return OrchestrationSwitch(default_state_path().parent)


def switch_modes() -> dict[str, str]:
    status = switch_owner().status(feature="herdr-review")
    return dict(status["features"]["herdr-review"]["harnesses"])


def reviewer_ids() -> tuple[str, ...]:
    catalog = read(CATALOG)
    values = catalog.get("reviewers")
    if not isinstance(values, list):
        raise RuntimeError("reviewer catalog is unavailable")
    result = tuple(item["reviewer_id"] for item in values if isinstance(item, dict) and isinstance(item.get("reviewer_id"), str))
    if not result:
        raise RuntimeError("reviewer catalog is empty")
    return result


def reviewer_command(model: str, reasoning: str) -> list[str]:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = sorted(
        (local_app_data / "OpenAI/Codex/bin").glob("*/codex.exe"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    codex_home = Path(os.environ.get("USERPROFILE", "")) / ".codex"
    if not candidates or not codex_home.is_dir():
        raise RuntimeError("direct Codex desktop runtime or subscription home is unavailable")
    return [
        str(Path(sys.executable).resolve()), str(BRIDGE.resolve()),
        "--model", model, "--reasoning", reasoning,
        "--codex-executable", str(candidates[0].resolve()),
        "--codex-home", str(codex_home.resolve()),
    ]


def reviewer_command_wsl(
    model: str, reasoning: str, *, codex_executable: Path, codex_home: Path,
) -> list[str]:
    codex = codex_executable.resolve()
    home = codex_home.resolve()
    if (
        not codex.is_file()
        or not codex.name.lower().startswith("codex")
        or not home.is_dir()
    ):
        raise RuntimeError("pinned WSL Codex runtime or subscription home is unavailable")
    return [
        str(Path(sys.executable).resolve()), str(BRIDGE.resolve()),
        "--model", model, "--reasoning", reasoning,
        "--codex-executable", str(codex),
        "--codex-home", str(home),
    ]


def herdr_command(executable: Path, *arguments: str) -> list[str]:
    command = [str(executable), *arguments]
    if executable.suffix.lower() in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *command]
    return command


def snapshot(operation: str, *, target: str = "windows") -> tuple[str, dict]:
    owner = switch_owner()
    value = {
        "schema_version": "1.0",
        "snapshot_id": uuid.uuid4().hex,
        "operation": operation,
        "target": target,
        "captured_at": utc_now(),
        "config": read_config(target),
        "runtime_lock": read_lock(target),
        "switch_state": read(owner.path) if owner.path.is_file() else None,
    }
    value["snapshot_sha256"] = digest({key: item for key, item in value.items() if key != "snapshot_sha256"})
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    atomic(SNAPSHOTS / f"{value['snapshot_id']}.json", value)
    return value["snapshot_id"], value


def write_receipt(operation: str, status: str, before: dict, after: dict, *, error: str = "") -> dict:
    value = {
        "schema_version": "1.0", "receipt_id": uuid.uuid4().hex,
        "operation": operation, "status": status, "recorded_at": utc_now(),
        "before_sha256": digest(before), "after_sha256": digest(after),
        "error_type": error[:128],
    }
    value["receipt_sha256"] = digest({key: item for key, item in value.items() if key != "receipt_sha256"})
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    atomic(RECEIPTS / f"{value['receipt_id']}.json", value)
    return value


def state() -> dict:
    config, lock = read_config(), read_lock()
    modes = switch_modes()
    bridge_hash = hashlib.sha256(BRIDGE.read_bytes()).hexdigest() if BRIDGE.is_file() else None
    reviewers = config.get("reviewers", {})
    commands = [
        item.get("command") for item in reviewers.values()
        if isinstance(item, dict) and isinstance(item.get("command"), list)
    ] if isinstance(reviewers, dict) else []
    commands_match = bool(commands) and len(commands) == len(reviewers) and all(command == commands[0] for command in commands)
    configured_codex_hash = None
    if commands_match and "--codex-executable" in commands[0]:
        index = commands[0].index("--codex-executable") + 1
        if index < len(commands[0]):
            candidate = Path(commands[0][index])
            if candidate.is_file():
                configured_codex_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
    configuration_valid = (
        commands_match
        and bridge_hash == lock.get("reviewer_bridge_sha256")
        and configured_codex_hash == lock.get("codex_executable_sha256")
        and isinstance(lock.get("reviewer_profile"), dict)
    )
    consistent = (
        (
            config.get("mode") == "off"
            and lock.get("runnable") is False
            and all(mode == "off" for mode in modes.values())
        )
        or (
            config.get("mode") == "supervised"
            and lock.get("runnable") is True
            and lock.get("activation_state") == "supervised"
            and modes.get("codex") == "supervised"
            and all(mode == "off" for name, mode in modes.items() if name != "codex")
            and configuration_valid
        )
    )
    return {
        "mode": config.get("mode"), "runtime_runnable": lock.get("runnable"),
        "runtime_activation_state": lock.get("activation_state"),
        "schema_pinned": isinstance(lock.get("schema_sha256"), str),
        "configured_reviewers": len(config.get("reviewers", {})),
        "switch_modes": modes, "bridge_installed": BRIDGE.is_file(),
        "bridge_sha256": bridge_hash,
        "reviewer_configuration_sha256": digest(reviewers),
        "reviewer_profile_sha256": digest(lock.get("reviewer_profile")),
        "codex_executable_sha256": configured_codex_hash,
        "configuration_valid": configuration_valid,
        "runtime_overlay_present": runtime_config_path().is_file() and runtime_lock_path().is_file(),
        "consistent": consistent,
        "wsl": wsl_state(),
    }


def wsl_state() -> dict:
    config, lock = read_config("wsl"), read_lock("wsl")
    reviewers = config.get("reviewers", {})
    commands = [
        item.get("command") for item in reviewers.values()
        if isinstance(item, dict) and isinstance(item.get("command"), list)
    ] if isinstance(reviewers, dict) else []
    commands_match = (
        bool(commands)
        and len(commands) == len(reviewers)
        and all(command == commands[0] for command in commands)
    )
    target = config.get("targets", {}).get("wsl", {})
    executable = target.get("executable")
    lock_executable = lock.get("executables", {}).get("wsl")
    codex_hash = lock.get("codex_executable_sha256")
    bridge_hash = (
        hashlib.sha256(BRIDGE.read_bytes()).hexdigest()
        if BRIDGE.is_file() else None
    )
    paths_verified = False
    if os.name != "nt" and commands_match and "--codex-executable" in commands[0]:
        codex_path = Path(commands[0][commands[0].index("--codex-executable") + 1])
        herdr_path = Path(executable) if isinstance(executable, str) else None
        paths_verified = bool(
            codex_path.is_file()
            and herdr_path is not None
            and herdr_path.is_file()
            and hashlib.sha256(codex_path.read_bytes()).hexdigest() == codex_hash
        )
    configuration_valid = bool(
        commands_match
        and target.get("qualified") is True
        and isinstance(executable, str)
        and executable == lock_executable
        and bridge_hash == lock.get("reviewer_bridge_sha256")
        and isinstance(codex_hash, str)
        and re.fullmatch(r"[a-f0-9]{64}", codex_hash)
        and isinstance(lock.get("reviewer_profile"), dict)
    )
    overlay_present = (
        runtime_config_path("wsl").is_file()
        and runtime_lock_path("wsl").is_file()
    )
    consistent = bool(
        config.get("mode") == "off"
        and lock.get("runnable") is False
        and lock.get("activation_state") == "inactive"
    )
    return {
        "mode": config.get("mode"),
        "runtime_runnable": lock.get("runnable"),
        "runtime_activation_state": lock.get("activation_state"),
        "qualified": target.get("qualified") is True,
        "configured_reviewers": len(reviewers) if isinstance(reviewers, dict) else 0,
        "configuration_valid": configuration_valid,
        "runtime_paths_verified": paths_verified,
        "runtime_overlay_present": overlay_present,
        "consistent": consistent,
    }


def _configure_wsl(
    *, model: str, reasoning: str, herdr_executable: Path,
    codex_executable: Path, codex_home: Path,
) -> dict:
    if os.name == "nt":
        raise RuntimeError("configure-wsl must run inside the WSL target")
    if not MODEL_PATTERN.fullmatch(model):
        raise RuntimeError("invalid model identifier")
    if not BRIDGE.is_file():
        raise RuntimeError("permanent reviewer bridge is unavailable")
    herdr = herdr_executable.resolve()
    if probe(herdr, target="wsl", lock_path=LOCK).get("status") != "READY":
        raise RuntimeError("WSL Herdr runtime qualification failed")
    command = reviewer_command_wsl(
        model, reasoning, codex_executable=codex_executable,
        codex_home=codex_home,
    )
    version = subprocess.run(
        [command[-3], "--version"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if version.returncode != 0 or not version.stdout.strip().startswith("codex-cli "):
        raise RuntimeError("WSL Codex runtime qualification failed")
    prior_state = wsl_state()
    config, lock = read_config("wsl"), read_lock("wsl")
    portable_lock = read(LOCK)
    desired_reviewers = {name: {"command": command} for name in reviewer_ids()}
    codex_hash = hashlib.sha256(Path(command[-3]).read_bytes()).hexdigest()
    bridge_hash = hashlib.sha256(BRIDGE.read_bytes()).hexdigest()
    profile = {
        "provider": "openai-codex", "auth": "chatgpt-subscription",
        "model": model, "reasoning_effort": reasoning,
        "codex_version": version.stdout.strip(),
    }
    if (
        config.get("mode") == "off"
        and config.get("targets", {}).get("wsl") == {
            "executable": str(herdr), "qualified": True,
        }
        and config.get("reviewers") == desired_reviewers
        and lock.get("executables", {}).get("wsl") == str(herdr)
        and lock.get("codex_executable_sha256") == codex_hash
        and lock.get("reviewer_bridge_sha256") == bridge_hash
        and lock.get("reviewer_profile") == profile
        and lock.get("schema_sha256") == portable_lock.get("schema_sha256")
        and prior_state["consistent"]
        and prior_state["runtime_paths_verified"]
    ):
        return {"changed": False, "status": "CONFIGURED_INACTIVE", "state": prior_state}
    snapshot_id, previous = snapshot("configure-wsl", target="wsl")
    try:
        lock = portable_lock
        config["mode"] = "off"
        config["targets"]["wsl"] = {
            "executable": str(herdr), "qualified": True,
        }
        config["reviewers"] = desired_reviewers
        lock["executables"]["wsl"] = str(herdr)
        lock["runnable"] = False
        lock["activation_state"] = "inactive"
        lock["codex_executable_sha256"] = codex_hash
        lock["reviewer_bridge_sha256"] = bridge_hash
        lock["reviewer_profile"] = profile
        atomic(runtime_config_path("wsl"), config)
        atomic(runtime_lock_path("wsl"), lock)
        after = wsl_state()
        if not after["consistent"] or not after["runtime_paths_verified"]:
            raise RuntimeError("post-configuration verification failed")
    except Exception as exc:
        restore(runtime_config_path("wsl"), previous["config"])
        restore(runtime_lock_path("wsl"), previous["runtime_lock"])
        rolled_back = wsl_state()
        write_receipt(
            "configure-wsl", "ROLLED_BACK", prior_state, rolled_back,
            error=type(exc).__name__,
        )
        raise RuntimeError("WSL configuration failed and was rolled back") from exc
    receipt = write_receipt(
        "configure-wsl", "COMPLETED", prior_state, after,
    )
    return {
        "changed": True,
        "status": "CONFIGURED_INACTIVE",
        "snapshot_id": snapshot_id,
        "receipt": receipt,
        "state": after,
    }


def configure_wsl(**kwargs) -> dict:
    with OperationLock():
        return _configure_wsl(**kwargs)


def _configure_windows(*, model: str, reasoning: str) -> dict:
    if not MODEL_PATTERN.fullmatch(model):
        raise RuntimeError("invalid model identifier")
    if not BRIDGE.is_file():
        raise RuntimeError("permanent reviewer bridge is unavailable")
    prior_state = state()
    if prior_state["mode"] != "off" or any(mode != "off" for mode in prior_state["switch_modes"].values()):
        raise RuntimeError("deactivate Herdr before changing its persistent reviewer configuration")
    config, lock = read_config(), read_lock()
    portable_lock = read(LOCK)
    direct_value = portable_lock.get("executables", {}).get("windows_direct")
    configured_value = config["targets"]["windows"]["executable"]
    executable = Path(direct_value or configured_value)
    result = probe(executable, lock_path=LOCK)
    if result.get("status") != "READY":
        raise RuntimeError("Herdr runtime qualification failed")
    command = reviewer_command(model, reasoning)
    desired_reviewers = {name: {"command": command} for name in reviewer_ids()}
    codex_hash = hashlib.sha256(Path(command[-3]).read_bytes()).hexdigest()
    bridge_hash = hashlib.sha256(BRIDGE.read_bytes()).hexdigest()
    profile = {
        "provider": "openai-codex", "auth": "chatgpt-subscription",
        "model": model, "reasoning_effort": reasoning,
    }
    if (
        config.get("targets", {}).get("windows", {}).get("executable") == str(executable.resolve())
        and config.get("targets", {}).get("windows", {}).get("qualified") is True
        and
        config.get("reviewers") == desired_reviewers
        and lock.get("codex_executable_sha256") == codex_hash
        and lock.get("reviewer_bridge_sha256") == bridge_hash
        and lock.get("reviewer_profile") == profile
        and lock.get("schema_sha256") == portable_lock.get("schema_sha256")
    ):
        return {"changed": False, "status": "CONFIGURED", "state": prior_state}
    snapshot_id, previous = snapshot("configure-windows")
    try:
        lock = portable_lock
        config["targets"]["windows"] = {
            "executable": str(executable.resolve()),
            "qualified": True,
        }
        config["reviewers"] = desired_reviewers
        lock["codex_executable_sha256"] = codex_hash
        lock["reviewer_bridge_sha256"] = bridge_hash
        lock["reviewer_profile"] = profile
        atomic(runtime_config_path(), config)
        atomic(runtime_lock_path(), lock)
        after = state()
        if not after["consistent"]:
            raise RuntimeError("post-configuration verification failed")
    except Exception as exc:
        restore(runtime_config_path(), previous["config"])
        restore(runtime_lock_path(), previous["runtime_lock"])
        rolled_back = state()
        write_receipt("configure-windows", "ROLLED_BACK", prior_state, rolled_back, error=type(exc).__name__)
        raise RuntimeError("configuration failed and was rolled back") from exc
    receipt = write_receipt("configure-windows", "COMPLETED", prior_state, after)
    return {"changed": True, "status": "CONFIGURED", "snapshot_id": snapshot_id, "receipt": receipt, "state": after}


def configure_windows(*, model: str, reasoning: str) -> dict:
    with OperationLock():
        return _configure_windows(model=model, reasoning=reasoning)


def _activate(*, harness: str, model: str, reasoning: str) -> dict:
    if harness != "codex":
        raise RuntimeError("initial activation is restricted to the codex harness")
    if not MODEL_PATTERN.fullmatch(model):
        raise RuntimeError("invalid model identifier")
    if not BRIDGE.is_file():
        raise RuntimeError("permanent reviewer bridge is unavailable")
    executable = Path(read_config()["targets"]["windows"]["executable"])
    result = probe(executable)
    if result.get("status") != "READY":
        raise RuntimeError("Herdr runtime qualification failed")
    prior_state = state()
    command = reviewer_command(model, reasoning)
    config = read_config()
    lock = read_lock()
    desired_reviewers = {name: {"command": command} for name in reviewer_ids()}
    codex_hash = hashlib.sha256(Path(command[-3]).read_bytes()).hexdigest()
    bridge_hash = hashlib.sha256(BRIDGE.read_bytes()).hexdigest()
    expected_profile = {
        "provider": "openai-codex", "auth": "chatgpt-subscription",
        "model": model, "reasoning_effort": reasoning,
    }
    if (
        config.get("reviewers") != desired_reviewers
        or lock.get("codex_executable_sha256") != codex_hash
        or lock.get("reviewer_bridge_sha256") != bridge_hash
        or lock.get("reviewer_profile") != expected_profile
    ):
        raise RuntimeError("persistent Windows reviewer is not configured; run configure-windows first")
    if (
        prior_state["consistent"] and config.get("mode") == "supervised"
        and config.get("reviewers") == desired_reviewers
        and prior_state["switch_modes"].get("codex") == "supervised"
    ):
        return {"changed": False, "status": "SUPERVISED", "state": prior_state}
    snapshot_id, previous = snapshot("activate")
    owner = switch_owner()
    try:
        config["mode"] = "supervised"
        lock["runnable"] = True
        lock["activation_state"] = "supervised"
        atomic(runtime_config_path(), config)
        atomic(runtime_lock_path(), lock)
        from prime_bridge.harness_registry import UNIVERSAL_HARNESS_REGISTRY
        owner.set("herdr-review", "off", UNIVERSAL_HARNESS_REGISTRY)
        owner.set("herdr-review", "supervised", ("codex",))
        after = state()
        if not after["consistent"] or probe(executable).get("runnable") is not True:
            raise RuntimeError("post-activation verification failed")
    except Exception as exc:
        restore(runtime_config_path(), previous["config"])
        restore(runtime_lock_path(), previous["runtime_lock"])
        restore(owner.path, previous["switch_state"])
        rolled_back = state()
        write_receipt("activate", "ROLLED_BACK", prior_state, rolled_back, error=type(exc).__name__)
        raise RuntimeError("activation failed and configuration was rolled back") from exc
    receipt = write_receipt("activate", "COMPLETED", prior_state, after)
    return {"changed": True, "status": "SUPERVISED", "snapshot_id": snapshot_id, "receipt": receipt, "state": after}


def activate(*, harness: str, model: str, reasoning: str) -> dict:
    with OperationLock():
        return _activate(harness=harness, model=model, reasoning=reasoning)


def evaluator_sessions(executable: Path) -> list[str]:
    result = subprocess.run(herdr_command(executable, "session", "list"), capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        return []
    return [line.split()[0] for line in result.stdout.splitlines() if line.startswith("eval-")]


def _deactivate() -> dict:
    prior_state = state()
    if (
        prior_state["consistent"] and prior_state["mode"] == "off"
        and not any(mode != "off" for mode in prior_state["switch_modes"].values())
    ):
        return {"changed": False, "status": "OFF", "state": prior_state}
    snapshot_id, previous = snapshot("deactivate")
    owner = switch_owner()
    config, lock = read_config(), read_lock()
    from prime_bridge.harness_registry import UNIVERSAL_HARNESS_REGISTRY
    owner.set("herdr-review", "off", UNIVERSAL_HARNESS_REGISTRY)
    config["mode"] = "off"
    lock["runnable"] = False
    lock["activation_state"] = "inactive"
    atomic(runtime_config_path(), config)
    atomic(runtime_lock_path(), lock)
    executable = Path(config["targets"]["windows"]["executable"])
    stopped = []
    if executable.is_file():
        for name in evaluator_sessions(executable):
            subprocess.run(herdr_command(executable, "session", "stop", name), capture_output=True, timeout=15, check=False)
            deleted = subprocess.run(herdr_command(executable, "session", "delete", "--json", name), capture_output=True, timeout=15, check=False)
            stopped.append({"session_sha256": hashlib.sha256(name.encode()).hexdigest(), "deleted": deleted.returncode == 0})
    after = state()
    if not after["consistent"]:
        # Deactivation failures remain fail-closed; never restore an active gate.
        raise RuntimeError("deactivation is fail-closed but final state is inconsistent")
    receipt = write_receipt("deactivate", "COMPLETED", prior_state, after)
    return {"changed": True, "status": "OFF", "snapshot_id": snapshot_id, "receipt": receipt, "sessions": stopped, "state": after}


def deactivate() -> dict:
    with OperationLock():
        return _deactivate()


def _rollback(snapshot_id: str | None) -> dict:
    candidates = sorted(SNAPSHOTS.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    path = SNAPSHOTS / f"{snapshot_id}.json" if snapshot_id else (candidates[0] if candidates else None)
    if path is None or not path.is_file():
        raise RuntimeError("rollback snapshot is unavailable")
    previous = read(path)
    target = previous.get("target", "windows")
    if target not in {"windows", "wsl"}:
        raise RuntimeError("rollback snapshot target is invalid")
    state_reader = state if target == "windows" else wsl_state
    before = state_reader()
    owner = switch_owner()
    restore(runtime_config_path(target), previous["config"])
    restore(runtime_lock_path(target), previous["runtime_lock"])
    restore(owner.path, previous["switch_state"])
    after = state_reader()
    receipt = write_receipt("rollback", "COMPLETED", before, after)
    return {"changed": True, "status": "ROLLED_BACK", "snapshot_id": previous["snapshot_id"], "receipt": receipt, "state": after}


def rollback(snapshot_id: str | None) -> dict:
    with OperationLock():
        return _rollback(snapshot_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    probe_parser = sub.add_parser("probe")
    probe_parser.add_argument("--executable", type=Path)
    activate_parser = sub.add_parser("activate")
    activate_parser.add_argument("--harness", default="codex")
    activate_parser.add_argument("--model", default="gpt-5.3-codex-spark")
    activate_parser.add_argument("--reasoning", choices=["low", "medium", "high"], default="low")
    activate_parser.add_argument("--confirm", required=True)
    configure_parser = sub.add_parser("configure-windows")
    configure_parser.add_argument("--model", default="gpt-5.3-codex-spark")
    configure_parser.add_argument("--reasoning", choices=["low", "medium", "high"], default="low")
    configure_parser.add_argument("--confirm", required=True)
    configure_wsl_parser = sub.add_parser("configure-wsl")
    configure_wsl_parser.add_argument("--herdr-executable", type=Path, required=True)
    configure_wsl_parser.add_argument("--codex-executable", type=Path, required=True)
    configure_wsl_parser.add_argument(
        "--codex-home", type=Path, default=Path.home() / ".codex",
    )
    configure_wsl_parser.add_argument("--model", default="gpt-5.3-codex-spark")
    configure_wsl_parser.add_argument(
        "--reasoning", choices=["low", "medium", "high"], default="low",
    )
    configure_wsl_parser.add_argument("--confirm", required=True)
    deactivate_parser = sub.add_parser("deactivate")
    deactivate_parser.add_argument("--confirm", required=True)
    rollback_parser = sub.add_parser("rollback")
    rollback_parser.add_argument("--snapshot-id")
    rollback_parser.add_argument("--confirm", required=True)
    mode = sub.add_parser("set-mode")
    mode.add_argument("mode", choices=sorted(MODES))
    mode.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(state(), sort_keys=True))
        return 0
    if args.command == "probe":
        print(json.dumps(probe(args.executable), sort_keys=True))
        return 0
    if args.command == "activate":
        expected = f"activate-herdr-review:supervised:{args.harness}"
        if args.confirm != expected:
            raise SystemExit("exact --confirm token required: " + expected)
        print(json.dumps(activate(harness=args.harness, model=args.model, reasoning=args.reasoning), sort_keys=True))
        return 0
    if args.command == "configure-windows":
        expected = "configure-herdr-review:windows:codex"
        if args.confirm != expected:
            raise SystemExit("exact --confirm token required: " + expected)
        print(json.dumps(configure_windows(model=args.model, reasoning=args.reasoning), sort_keys=True))
        return 0
    if args.command == "configure-wsl":
        expected = "configure-herdr-review:wsl:codex"
        if args.confirm != expected:
            raise SystemExit("exact --confirm token required: " + expected)
        print(json.dumps(configure_wsl(
            model=args.model,
            reasoning=args.reasoning,
            herdr_executable=args.herdr_executable,
            codex_executable=args.codex_executable,
            codex_home=args.codex_home,
        ), sort_keys=True))
        return 0
    if args.command == "deactivate":
        expected = "deactivate-herdr-review"
        if args.confirm != expected:
            raise SystemExit("exact --confirm token required: " + expected)
        print(json.dumps(deactivate(), sort_keys=True))
        return 0
    if args.command == "rollback":
        expected = "rollback-herdr-review"
        if args.confirm != expected:
            raise SystemExit("exact --confirm token required: " + expected)
        print(json.dumps(rollback(args.snapshot_id), sort_keys=True))
        return 0
    # Legacy path is constrained to the unified fail-closed deactivation operation.
    expected = f"set-herdr-review-mode:{args.mode}"
    if args.confirm != expected:
        raise SystemExit("exact --confirm token required: " + expected)
    if args.mode != "off":
        raise SystemExit("use activate for supervised mode; full mode is intentionally unavailable")
    print(json.dumps(deactivate(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
