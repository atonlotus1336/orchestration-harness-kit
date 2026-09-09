"""Provider-independent, host-owned evidence collection. No model or Git writes.

The host supplies an already-authorized task and routing decision. They are
bindings, not new grants of authority. Only this adapter executes/checks tests;
caller-supplied exit statuses are never accepted. Receipts are evidence, not
permission to apply patches, complete jobs, publish, or mutate Git history.
"""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
from importlib.metadata import version as package_version
import json
import os
import platform
import re
from pathlib import Path, PurePosixPath
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

from jsonschema import Draft7Validator

from .adaptive_review.context import DeterministicCheckResult, ReviewFact, evaluate_change_contract
from .adaptive_review.event_hooks import evaluate_event_hooks
from .adaptive_review.models import Pipeline, Scope
from .adaptive_review.policy import load_policy
from .adaptive_review.receipts import Receipt, write_receipt_atomic, SAFE_SEGMENT
from .adaptive_review.router import select_review
from .constrained_test_runner import TestCommandPolicy
from .omp_task_packet import OmpTaskPacket, verification_command_id
from .task_authorization import authorization_matches

ROOT = Path(__file__).resolve().parents[1]
HARNESSES = frozenset({"hermes", "codex", "claude-code", "moa", "opencode", "pi", "antigravity", "cursor", "dsh"})
LANES = frozenset({"T0_DETERMINISTIC", "T1_NATIVE", "T2_OMP_LITE", "T3_PRIME_OMP", "T4_AUTONOMOUS"})
VERSION = "1.0"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PATHS = 20000
MAX_OWNER_RECEIPT_BYTES = 64 * 1024


class EvidenceError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _lifecycle_declaration(value: Mapping[str, Any], *, root: Path | None = None,
                           packet: OmpTaskPacket | None = None, harness: str | None = None,
                           authorization: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate an existing lifecycle owner without creating a second owner."""
    if not isinstance(value, Mapping):
        raise EvidenceError("existing lifecycle binding required")
    kind = value.get("kind")
    if kind == "receipt":
        if set(value) != {"kind", "owner_id", "receipt_path", "receipt_sha256"}:
            raise EvidenceError("receipt lifecycle binding is not closed")
        owner_id, expected = value.get("owner_id"), value.get("receipt_sha256")
        if not isinstance(owner_id, str) or not SAFE_SEGMENT.fullmatch(owner_id):
            raise EvidenceError("invalid lifecycle receipt identity")
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise EvidenceError("invalid lifecycle receipt digest")
        path = Path(str(value.get("receipt_path", ""))).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > MAX_OWNER_RECEIPT_BYTES:
            raise EvidenceError("unsupported lifecycle receipt")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise EvidenceError("stale lifecycle receipt")
        try:
            owner = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise EvidenceError("lifecycle owner is not a JSON receipt") from exc
        if not isinstance(owner, dict) or owner.get("execution_id") != owner_id:
            raise EvidenceError("lifecycle receipt identity mismatch")
        if root is not None and packet is not None and harness is not None and authorization is not None:
            scope = owner.get("fallback_scope")
            if (owner.get("schema_version") != "2.0"
                    or owner.get("outcome") != "fallback"
                    or owner.get("native_fallback_authorized") is not True
                    or owner.get("harness") != harness
                    or owner.get("project") != authorization.get("project")
                    or not isinstance(owner.get("workspace"), str)
                    or Path(owner["workspace"]).resolve() != root
                    or owner.get("task_sha256") != hashlib.sha256(packet.objective.encode()).hexdigest()
                    or not isinstance(scope, dict)
                    or sorted(scope.get("paths", [])) != sorted(packet.allowed_paths)):
                raise EvidenceError("lifecycle receipt task continuity mismatch")
        return {"kind": kind, "owner_id": owner_id, "receipt_path": str(path), "receipt_sha256": expected}
    if kind == "job_ledger":
        if set(value) != {"kind", "project", "session_id"}:
            raise EvidenceError("job-ledger lifecycle binding is not closed")
        project, session_id = value.get("project"), value.get("session_id")
        if not isinstance(project, str) or not project or not isinstance(session_id, str) or not session_id:
            raise EvidenceError("invalid job-ledger lifecycle identity")
        return {"kind": kind, "project": project, "session_id": session_id}
    raise EvidenceError("unsupported lifecycle owner")


def _filter_inert_observation(args: tuple[str, ...]) -> bool:
    """Closed list of Git reads that cannot request worktree content conversion."""
    if not args:
        return False
    command = args[0]
    if command == "rev-parse":
        return all(value in {"rev-parse", "--show-toplevel", "--verify", "HEAD"} for value in args)
    if command == "config":
        return args == ("config", "--bool", "core.filemode")
    if command == "ls-files":
        allowed = {"ls-files", "--stage", "--modified", "--deleted", "--others", "--exclude-standard", "-z"}
        return all(value in allowed for value in args)
    if command == "cat-file" and len(args) == 3 and args[1] in {"-s", "blob"}:
        return re.fullmatch(r"[a-f0-9]{40,64}", args[2]) is not None
    if command == "diff":
        objects = [value for value in args if re.fullmatch(r"[a-f0-9]{40,64}", value)]
        required = {"--name-only", "--no-ext-diff", "--no-textconv", "--no-renames", "-z", "--"}
        return len(objects) == 2 and required.issubset(args)
    return False


def refuse_git_filters(root: Path, observation: tuple[str, ...] | None = None) -> None:
    """Refuse repository-local or inherited active filters before Git runs."""
    try:
        local = subprocess.run(["git", "-C", str(root), "config", "--local", "--get-regexp",
                                r"^filter\..*\.(clean|smudge|process)$"],
                               capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("checkout drivers unknown; use broker isolation") from exc
    if local.returncode == 0 or local.returncode != 1:
        raise EvidenceError("checkout drivers present or unknown; use broker isolation")
    try:
        effective = subprocess.run(["git", "-C", str(root), "config", "--get-regexp",
                                   r"^filter\..*\.(clean|smudge|process)$"],
                                  capture_output=True, timeout=10, check=False)
        if effective.returncode == 1:
            return
        if effective.returncode != 0:
            raise EvidenceError("checkout drivers present or unknown; use broker isolation")
        if observation is not None and _filter_inert_observation(observation):
            return
        base = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root)]
        tracked = subprocess.run([*base, "ls-files", "-z"], capture_output=True,
                                 timeout=30, check=False)
        untracked = subprocess.run([*base, "ls-files", "--others", "--exclude-standard", "-z"],
                                   capture_output=True, timeout=30, check=False)
        if tracked.returncode or untracked.returncode:
            raise EvidenceError("checkout drivers present or unknown; use broker isolation")
        names = tracked.stdout + untracked.stdout
        if len(names) > 16 * 1024 * 1024:
            raise EvidenceError("checkout driver scope exceeds evidence budget")
        if not names:
            return
        attributes = subprocess.run([*base, "check-attr", "-z", "--stdin", "filter"],
                                    input=names, capture_output=True, timeout=30, check=False)
        fields = attributes.stdout.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        if (attributes.returncode or len(attributes.stdout) > 16 * 1024 * 1024
                or len(fields) % 3 != 0
                or any(value not in {b"unspecified", b"unset"} for value in fields[2::3])):
            raise EvidenceError("checkout drivers present or unknown; use broker isolation")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("checkout drivers unknown; use broker isolation") from exc


def _git(root: Path, *args: str) -> bytes:
    refuse_git_filters(root, tuple(args))
    result = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
                            cwd=root, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise EvidenceError("Git observation failed: " + args[0])
    if len(result.stdout) > 16 * 1024 * 1024:
        raise EvidenceError("Git observation exceeds evidence budget")
    return result.stdout


def _path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if not parts or name in {".", ""} or "\\" in name or ":" in name or name.startswith("/") or ".." in parts:
        raise EvidenceError("nonportable or escaping evidence path")
    if any(p.casefold() in {".git", ".env", "secrets", "credentials"} or p.casefold().startswith(".env.") for p in parts):
        raise EvidenceError("sensitive evidence path refused")
    target = root.joinpath(*parts)
    for candidate in (target, *target.parents):
        if candidate == root:
            break
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise EvidenceError("symlink/junction evidence paths refused")
    if not target.resolve().is_relative_to(root):
        raise EvidenceError("evidence path escaped root")
    return target


def _file_hash(root: Path, name: str) -> str:
    target = _path(root, name)
    if not target.exists():
        return "ABSENT"
    if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
        raise EvidenceError("unsupported or oversized evidence file")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _opaque_metadata(root: Path, name: str) -> str:
    """Observe unrelated sensitive/symlink entries without opening their bytes."""
    target = root
    for part in PurePosixPath(name).parts:
        if part in {"", ".", ".."} or ":" in part or "\\" in part or part.startswith("/"):
            raise EvidenceError("cannot safely observe nonportable Git path")
        target = target / part
        try:
            info = target.lstat()
        except FileNotFoundError:
            return "ABSENT"
        if stat.S_ISLNK(info.st_mode) or (hasattr(target, "is_junction") and target.is_junction()):
            break
    return "METADATA_ONLY:" + digest([info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns])


def snapshot(root: Path, declared: tuple[str, ...], *, preserve_declared_bytes: bool = False) -> dict[str, Any]:
    """Index blob identities + dirty/untracked byte hashes; no whole-vault crawl.

    Ignored files are outside observation, except explicitly declared paths.
    Generated evidence is excluded to prevent receipts observing themselves.
    This detects modifications to already-dirty files, not just status changes.
    """
    root = root.resolve(strict=True)
    top = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if root != top:
        raise EvidenceError("workspace must be the exact Git repository root")
    head = _git(root, "rev-parse", "--verify", "HEAD").decode().strip()
    index: dict[str, str] = {}
    for row in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not row:
            continue
        meta, raw_path = row.split(b"\t", 1)
        mode, blob, stage = meta.decode().split()
        if stage != "0":
            raise EvidenceError("unmerged index cannot produce evidence")
        name = os.fsdecode(raw_path)
        if not name.startswith(".planning/review-evidence/"):
            index[name] = mode + ":" + blob
    names = set(declared)
    dirty_or_untracked = set()
    for args in (("ls-files", "--modified", "--deleted", "-z"),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        observed = {os.fsdecode(p) for p in _git(root, *args).split(b"\0") if p}
        names.update(observed)
        dirty_or_untracked.update(observed)
    names = {p for p in names if not p.startswith(".planning/review-evidence/")}
    if len(names) + len(index) > MAX_PATHS:
        raise EvidenceError("path budget exceeded")
    contents = {}
    worktree_modes = {}
    filemode = _git(root, "config", "--bool", "core.filemode").strip() == b"true"
    unobserved = []
    baseline_bytes = {}
    remaining = 64 * 1024 * 1024
    for name in sorted(names, key=lambda p: (p not in declared, p)):
        try:
            target = _path(root, name)
            size = target.stat().st_size if target.exists() else 0
            if size > remaining:
                raise EvidenceError("content observation budget exceeded")
            contents[name] = _file_hash(root, name)
            mode = ("100755" if target.stat().st_mode & 0o100 else "100644") if contents[name] != "ABSENT" else None
            if mode is not None and not filemode:
                mode = index.get(name, "100644").split(":", 1)[0]
            worktree_modes[name] = mode
            if (preserve_declared_bytes and name in declared and contents[name] != "ABSENT"
                    and (name in dirty_or_untracked or name not in index)):
                raw = target.read_bytes()
                if hashlib.sha256(raw).hexdigest() != contents[name]:
                    raise EvidenceError("declared baseline changed during capture")
                baseline_bytes[name] = base64.b64encode(raw).decode("ascii")
            remaining -= size
        except EvidenceError:
            if name in declared:
                raise
            contents[name] = _opaque_metadata(root, name)
            unobserved.append(name)
    state = {"head": head, "index": index, "contents": contents,
             "worktree_modes": worktree_modes, "filemode": filemode,
             "unobserved_paths": sorted(unobserved)}
    result = {**state, "identity": digest(state)}
    if baseline_bytes:
        result["baseline_bytes"] = baseline_bytes
    return result


def changed_paths(root: Path, before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    changed: set[str] = set()
    if before["head"] != after["head"]:
        changed.update(os.fsdecode(p) for p in _git(root, "diff", "--name-only", "--no-ext-diff", "--no-textconv", "--no-renames", before["head"], after["head"], "-z", "--").split(b"\0") if p)
    for field in ("index", "contents", "worktree_modes"):
        a, b = before.get(field, {}), after.get(field, {})
        changed.update(p for p in set(a) | set(b) if a.get(p) != b.get(p))
    return sorted(changed)


def capabilities() -> dict[str, Any]:
    """Probe the exact OS network isolation used for every external command."""
    unshare = shutil.which("unshare") if sys.platform.startswith("linux") else None
    isolated = False
    if unshare:
        try:
            parent_namespace = os.readlink("/proc/self/ns/net")
            probe = subprocess.run([unshare, "--user", "--map-root-user", "--net", sys.executable, "-I", "-c",
                                    "import os,socket,json;print(json.dumps({'namespace':os.readlink('/proc/self/ns/net'),'interfaces':[n for _,n in socket.if_nameindex()]}))"],
                                   capture_output=True, timeout=5, check=False)
            observed = json.loads(probe.stdout) if probe.returncode == 0 else {}
            isolated = bool(observed.get("namespace") and observed["namespace"] != parent_namespace
                            and set(observed.get("interfaces", [])) == {"lo"})
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    return {"shell_free": True, "network_none": isolated,
            "environment_allowlist": True, "bounded_output": True,
            "process_group_timeout": os.name == "posix", "filesystem_sandbox": False,
            "isolation_executable": unshare if isolated else None}


def run_check(root: Path, packet: OmpTaskPacket, policy: TestCommandPolicy,
              required: tuple[str, ...] = ()) -> dict[str, Any]:
    """Run exactly the packet's allowlisted check. No raw shell or status input."""
    command_id = verification_command_id(packet)
    caps = capabilities() if command_id != "packet_static" or required else {
        "shell_free": True, "network_none": True, "environment_allowlist": True,
        "bounded_output": True, "process_group_timeout": False, "filesystem_sandbox": False,
    }
    missing = [name for name in required if caps.get(name) is not True]
    if missing:
        return {"command_id": command_id, "status": "CAPABILITY_REFUSED", "exit_code": None, "missing": missing, "capabilities": caps}
    if command_id == "packet_static":
        checked = []
        try:
            for name in packet.allowed_paths:
                target = _path(root, name)
                if not target.exists():
                    continue
                if target.stat().st_size > MAX_FILE_BYTES:
                    raise EvidenceError("static check byte budget exceeded")
                text = target.read_text(encoding="utf-8")
                if target.suffix == ".py":
                    compile(text, name, "exec")  # parse/compile only; never execute source
                elif target.suffix == ".json":
                    json.loads(text)
                else:
                    continue  # unsupported formats are not claimed as checked
                checked.append(name)
            for assertion in packet.semantic_assertions:
                kind, name, literal = assertion.split(":", 2)
                if name not in packet.allowed_paths or kind not in {"contains", "not_contains"}:
                    raise EvidenceError("unsupported static assertion")
                target = _path(root, name)
                if target.stat().st_size > MAX_FILE_BYTES:
                    raise EvidenceError("static assertion byte budget exceeded")
                text = target.read_text(encoding="utf-8")
                if (kind == "contains" and literal not in text) or (kind == "not_contains" and literal in text):
                    raise EvidenceError("packet-bound static assertion failed")
            return {"command_id": command_id, "status": "ok" if checked else "EMPTY_CHECK",
                    "exit_code": 0 if checked else None, "checked": checked,
                    "assertion_count": len(packet.semantic_assertions),
                    "kind": "static", "capabilities": caps}
        except (ValueError, SyntaxError, UnicodeError) as exc:
            return {"command_id": command_id, "status": "ok", "exit_code": 1,
                    "error_type": type(exc).__name__, "kind": "static", "capabilities": caps}
    return _run_isolated_command(root, policy.command(command_id), caps)


def run_host_acceptance(root: Path, test_path: Path, report_path: Path,
                        config_path: Path, timeout_seconds: float, *, required_capabilities: tuple[str, ...] = ()) -> dict[str, Any]:
    """Pinned review checks use the same capability owner as other evidence tests."""
    from .constrained_test_runner import TestCommand
    root = root.resolve(strict=True)
    test_path = test_path.resolve(strict=True)
    if not test_path.is_relative_to(root) or timeout_seconds <= 0:
        raise EvidenceError("invalid host acceptance execution boundary")
    command = TestCommand("host_acceptance",
        (sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-p", "no:cacheprovider",
         "-c", str(config_path), "-q", str(test_path), "--junitxml", str(report_path)),
        ".", ("PATH", "LANG", "LC_ALL", "SYSTEMROOT"), min(30, timeout_seconds), 1_000_000, "none")
    caps = capabilities()
    if not isinstance(required_capabilities, (tuple, list)) or any(not isinstance(v, str) for v in required_capabilities):
        raise EvidenceError("invalid required host capabilities")
    missing = [name for name in required_capabilities if caps.get(name) is not True]
    if missing:
        return {"command_id": "host_acceptance", "status": "CAPABILITY_REFUSED", "exit_code": None,
                "missing": missing, "capabilities": caps}
    return _run_isolated_command(root, command, caps)


def _run_isolated_command(root, command, caps):
    command_id = command.command_id
    # Loopback support is not silently widened to host networking.
    if command.network != "none" or not caps["network_none"] or not caps["process_group_timeout"]:
        return {"command_id": command_id, "status": "CAPABILITY_REFUSED", "exit_code": None,
                "missing": ["enforced_" + command.network + "_network"], "capabilities": caps}
    cwd = root if command.cwd == "." else _path(root, command.cwd)
    argv = list(command.argv)
    if Path(argv[0]).name in {"python", "python3", "python.exe"}:
        argv[0] = sys.executable
    else:
        resolved = shutil.which(argv[0])
        if not resolved:
            return {"command_id": command_id, "status": "RUNTIME_UNAVAILABLE", "exit_code": None}
        argv[0] = resolved
    # Never inherit HOME/PYTHONPATH/credential-bearing variables from the harness.
    env = {key: os.environ[key] for key in command.environment_allowlist
           if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"} and key in os.environ}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTEST_ADDOPTS": "-p no:cacheprovider"})
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="lapart-evidence-") as temp:
        env.update({"HOME": temp, "TMPDIR": temp})
        # Disk spooling bounds parent memory; kill the group on cap or timeout.
        with tempfile.TemporaryFile() as output:
            proc = subprocess.Popen([caps["isolation_executable"], "--user", "--map-root-user", "--net", *argv],
                                    cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            status = "ok"
            try:
                while proc.poll() is None:
                    if time.monotonic() - start > command.timeout_seconds:
                        status = "timeout"; break
                    if os.fstat(output.fileno()).st_size > command.output_cap_bytes:
                        status = "output_limit"; break
                    time.sleep(.02)
            finally:
                # Also stop background descendants after a successful parent exit.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            output.seek(0)
            raw = output.read(command.output_cap_bytes + 1)
            if len(raw) > command.output_cap_bytes:
                status = "output_limit"
    return {"command_id": command_id, "status": status,
            "exit_code": proc.returncode if status == "ok" else None,
            "assertion_failure": bool(status == "ok" and proc.returncode == 1 and
                                      (b"AssertionError" in raw or re.search(rb"\b[1-9][0-9]* failed\b", raw))),
            "argv_sha256": digest(argv), "output_sha256": hashlib.sha256(raw).hexdigest(),
            "output_bytes": len(raw), "elapsed_ms": int((time.monotonic() - start) * 1000),
            "kind": "test", "capabilities": caps}


def consumption(verdict: str, rollout_mode: str) -> dict[str, Any]:
    if verdict not in {"PASS", "FINDINGS", "INCONCLUSIVE"}:
        raise EvidenceError("unknown review verdict")
    if rollout_mode not in {"shadow", "advisory", "enforced_phase", "manual_system"}:
        raise EvidenceError("unknown review rollout")
    advisory = rollout_mode in {"shadow", "advisory"}
    return {"review_passed": verdict == "PASS", "advisory": advisory,
            "review_blocks_completion": not advisory and verdict != "PASS",
            "grants_execution_authority": False}


class EvidenceAdapter:
    """One shared adapter; harness name is attribution, never capability proof."""
    def __init__(self, root: Path, *, review_policy: Path | None = None,
                 test_policy: Path | None = None):
        self.root = root.resolve(strict=True)
        self.review_policy = load_policy(review_policy or ROOT / "config/adaptive-code-review-policy.json")
        self.test_policy = TestCommandPolicy.load(test_policy or ROOT / "config/omp-test-command-policy.json")
        self.validator = Draft7Validator(json.loads((ROOT / "schemas/change-evidence.schema.json").read_text(encoding="utf-8")))
        self.collector_fingerprint = digest({
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                "prime_bridge/evidence_adapter.py", "prime_bridge/omp_task_packet.py",
                "prime_bridge/adaptive_review/receipts.py", "prime_bridge/adaptive_review/context.py",
                "prime_bridge/adaptive_review/event_hooks.py",
                "prime_bridge/adaptive_review/models.py", "prime_bridge/adaptive_review/policy.py",
                "prime_bridge/adaptive_review/router.py", "prime_bridge/constrained_test_runner.py",
                "prime_bridge/evidence_scan.py", "prime_bridge/omp_policy.py",
                "prime_bridge/task_authorization.py", "schemas/change-evidence.schema.json",
            )
        } | {"python": platform.python_version(), "jsonschema": package_version("jsonschema")})

    def _write(self, value: dict[str, Any]) -> Path:
        evidence = dict(value)
        evidence.pop("evidence_sha256", None)
        evidence["evidence_sha256"] = digest(evidence)
        self._validate(evidence)
        return write_receipt_atomic(self.root, Receipt(
            "checkpoint", value["session_key"], value["run_id"], self.review_policy.fingerprint,
            {"deterministic": self.collector_fingerprint}, value.get("review_verdict", "INCONCLUSIVE"), evidence))

    def _validate(self, evidence: dict[str, Any]) -> None:
        errors = list(self.validator.iter_errors(evidence))
        if errors:
            raise EvidenceError("invalid evidence schema: " + errors[0].message)

    def read(self, path: Path) -> dict[str, Any]:
        path = path.resolve(strict=True)
        if not path.is_relative_to(self.root / ".planning/review-evidence"):
            raise EvidenceError("receipt is outside target evidence store")
        raw = json.loads(path.read_text(encoding="utf-8"))
        value = raw["evidence"]
        unsigned = {k: v for k, v in value.items() if k != "evidence_sha256"}
        if value.get("evidence_sha256") != digest(unsigned):
            raise EvidenceError("altered evidence receipt")
        self._validate(value)
        if value["repository_fingerprint"] != digest(str(self.root)):
            raise EvidenceError("receipt belongs to another repository location")
        if value["policy_fingerprint"] != self.review_policy.fingerprint or value["test_policy_fingerprint"] != self.test_policy.fingerprint:
            raise EvidenceError("stale evidence policy")
        if value["collector_fingerprint"] != self.collector_fingerprint:
            raise EvidenceError("stale collector implementation")
        if raw["verdict"] != value.get("review_verdict", "INCONCLUSIVE"):
            raise EvidenceError("receipt verdict mismatch")
        return value

    def begin(self, *, packet: OmpTaskPacket, harness: str, run_id: str,
              execution_value: Mapping[str, Any], authorization: Mapping[str, Any],
              lifecycle: Mapping[str, Any], required_capabilities: tuple[str, ...] = (),
              pre_fix: bool = False, session_binding_sha256: str | None = None) -> Path:
        if harness not in HARNESSES or not SAFE_SEGMENT.fullmatch(run_id):
            raise EvidenceError("invalid harness/run identity")
        if type(pre_fix) is not bool or not isinstance(required_capabilities, (tuple, list)) or any(not isinstance(c, str) for c in required_capabilities):
            raise EvidenceError("invalid capability or pre-fix request")
        if session_binding_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", session_binding_sha256):
            raise EvidenceError("invalid harness session binding")
        if packet.change_contract is None or execution_value.get("actual_lane") not in LANES:
            raise EvidenceError("canonical change contract and actual execution lane required")
        # Dataclass construction alone is not validation of the closed packet.
        OmpTaskPacket.from_mapping(packet.canonical_data())
        fingerprint = execution_value.get("policy_fingerprint", "")
        if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise EvidenceError("execution policy fingerprint required")
        if not authorization_matches(authorization, project=authorization.get("project"),
                                     declared_paths=packet.allowed_paths, policy_revision="governance-continuity-v1"):
            raise EvidenceError("authorization scope or policy mismatch")
        if authorization.get("task_sha256") != hashlib.sha256(packet.objective.encode()).hexdigest():
            raise EvidenceError("authorization objective mismatch")
        for name in packet.allowed_paths:
            _path(self.root, name)
        repository = digest(str(self.root))
        lifecycle = _lifecycle_declaration(
            lifecycle, root=self.root, packet=packet, harness=harness, authorization=authorization,
        )
        # The stable external delivery key cannot silently acquire a new scope.
        key = digest({"repository": repository, "harness": harness, "run_id": run_id})
        binding = {"packet_hash": packet.packet_hash, "authorization": dict(authorization),
                   "execution_value": dict(execution_value), "required_capabilities": list(required_capabilities),
                   "pre_fix": pre_fix, "lifecycle": lifecycle}
        if session_binding_sha256 is not None:
            binding["session_binding_sha256"] = session_binding_sha256
        dest = _path(self.root, f".planning/review-evidence/checkpoint/{key}/{run_id}/receipt.json")
        if dest.exists():
            old = self.read(dest)
            if old["binding"] != binding:
                raise EvidenceError("duplicate event changed its binding")
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Durable claims are receipt-local, not a competing job lifecycle store.
        claim = dest.parent / "begin.claim"
        try:
            fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise EvidenceError("begin already claimed; inspect interrupted receipt") from exc
        os.close(fd)
        before = snapshot(self.root, packet.allowed_paths, preserve_declared_bytes=True)
        pre = run_check(self.root, packet, self.test_policy, required_capabilities) if pre_fix else None
        if pre and snapshot(self.root, packet.allowed_paths)["identity"] != before["identity"]:
            raise EvidenceError("pre-fix verification changed project files")
        return self._write({"schema_version": VERSION, "stage": "begun", "run_id": run_id,
                            "session_key": key, "harness": harness, "repository_fingerprint": repository,
                            "policy_fingerprint": self.review_policy.fingerprint,
                            "test_policy_fingerprint": self.test_policy.fingerprint,
                            "collector_fingerprint": self.collector_fingerprint,
                            "binding": binding, "packet": packet.canonical_data(), "before": before,
                            "pre_fix_check": pre, "model_calls": 0})

    def finish(self, receipt_path: Path, *, job_ledger: Any = None, job_id: str | None = None) -> dict[str, Any]:
        receipt_path = receipt_path.resolve(strict=True)
        value = self.read(receipt_path)
        packet = OmpTaskPacket.from_mapping(value["packet"])
        if packet.packet_hash != value["binding"]["packet_hash"]:
            raise EvidenceError("packet binding mismatch")
        lifecycle = _lifecycle_declaration(
            value["binding"]["lifecycle"], root=self.root, packet=packet,
            harness=value["harness"], authorization=value["binding"]["authorization"],
        )
        lifecycle_binding = {"kind": lifecycle["kind"], "job_id": None, "audited": False}
        if lifecycle["kind"] == "job_ledger":
            if job_id is None or job_ledger is None:
                raise EvidenceError("existing job ledger and job identity required")
            job = job_ledger.get(job_id)
            if (Path(job["workspace"]).resolve() != self.root
                    or job["project"] != lifecycle["project"]
                    or job["session_id"] != lifecycle["session_id"]):
                raise EvidenceError("job identity mismatch")
            lifecycle_binding["job_id"] = job_id
        elif job_id is not None or job_ledger is not None:
            raise EvidenceError("receipt-owned lifecycle cannot acquire a job binding")
        else:
            lifecycle_binding["audited"] = True
        current = snapshot(self.root, packet.allowed_paths)
        if value["stage"] == "finished":
            if current["identity"] != value["after"]["identity"]:
                raise EvidenceError("duplicate event has stale content")
            if (value["lifecycle_binding"].get("kind") != lifecycle_binding["kind"]
                    or value["lifecycle_binding"].get("job_id") != lifecycle_binding["job_id"]):
                raise EvidenceError("duplicate event changed lifecycle binding")
            return value
        claim = receipt_path.parent / "finish.claim"
        try:
            fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise EvidenceError("finish already claimed; do not replay commands") from exc
        os.close(fd)
        try:
            outside_before_check = set(changed_paths(self.root, value["before"], current)) - set(packet.allowed_paths)
            if outside_before_check:
                check = {"command_id": verification_command_id(packet), "status": "SCOPE_REFUSED", "exit_code": None}
            else:
                check = run_check(self.root, packet, self.test_policy, tuple(value["binding"]["required_capabilities"]))
            after = snapshot(self.root, packet.allowed_paths)
            changed = changed_paths(self.root, value["before"], after)
            verification_mutations = changed_paths(self.root, current, after)
            # Packet regression statuses are claims, never independent measurements.
            contract = replace(packet.change_contract, regression_test_evidence=None)
            measured = []
            for phase, result in (("pre_fix", value["pre_fix_check"]), ("post_fix", check)):
                if result and result["status"] == "ok" and type(result["exit_code"]) is int:
                    if phase == "pre_fix" and result.get("assertion_failure") is not True:
                        continue  # setup errors/empty suites are not a regression reproduction
                    measured.append(DeterministicCheckResult(result["command_id"], result["exit_code"], phase,
                                                            contract.acceptance_criteria[0] if contract.acceptance_criteria else None))
            tests = measured if check.get("kind") == "test" else []
            static = measured if check.get("kind") == "static" else []
            evaluation = evaluate_change_contract(contract, actual_changed_paths=changed,
                                                  deterministic_test_results=tests, static_analysis_results=static)
            facts = list(evaluation.facts)
            if packet.risk_tier != "low" or len(packet.allowed_paths) > 2:
                facts.append(ReviewFact("nontrivial_scope", "S1"))
            if check.get("kind") == "static" and not packet.semantic_assertions:
                facts.append(ReviewFact("syntax_is_not_acceptance_evidence", "S1"))
            decision = select_review(facts, self.review_policy, Scope.CHECKPOINT)
            failed = check["status"] == "ok" and check["exit_code"] != 0
            incomplete = check["status"] != "ok" or evaluation.inconclusive or not changed
            verdict = "FINDINGS" if failed or evaluation.findings or verification_mutations else "PASS"
            if incomplete or decision.pipeline is not Pipeline.S0 or decision.verdict.value == "INCONCLUSIVE":
                verdict = "INCONCLUSIVE"
            hard_failure = bool(failed or verification_mutations or evaluation.changed_paths_outside_declared_scope)
            execution_evidence_status = "FAILED" if hard_failure else ("INCONCLUSIVE" if check["status"] != "ok" else "VERIFIED")
            hooks = evaluate_event_hooks(
                pipeline=decision.pipeline,
                declared_paths=packet.allowed_paths,
                actual_changed_paths=changed,
                before_identity=value["before"]["identity"],
                after_identity=after["identity"],
                current_identity=after["identity"],
                evidence_status=execution_evidence_status,
                findings=(finding.canonical_data() for finding in evaluation.findings),
                verification_mutations=verification_mutations,
                policy=self.review_policy.data,
            )
            value.update({"stage": "finished", "after": after, "actual_changed_paths": changed,
                          "verification_mutations": verification_mutations, "check": check,
                          "change_contract_evidence": evaluation.receipt_data(),
                          "contract_findings": [f.canonical_data() for f in evaluation.findings],
                          "review_pipeline": decision.pipeline.value, "review_verdict": verdict,
                          "review_reasons": list(decision.reasons), "review_exclusions": list(decision.exclusions),
                          "reviewer_invoked": False, "semantic_review_required": decision.pipeline is not Pipeline.S0,
                          "consumption": consumption(verdict, self.review_policy.rollout_mode.value),
                          "execution_evidence_status": execution_evidence_status,
                          "evaluation_hooks": hooks,
                          "lifecycle_binding": lifecycle_binding,
                          "limitations": ["ignored files not observed unless declared", "metadata-only paths are not content verified", "no filesystem sandbox", "no semantic reviewer invoked"]})
            if job_id is not None:
                job_ledger.note(job_id, "change_evidence", {"run_id": value["run_id"], "content_id": after["identity"],
                                                         "verdict": verdict, "packet_hash": packet.packet_hash})
                value["lifecycle_binding"]["audited"] = True
            self._write(value)
        except Exception as exc:
            # The claim remains at-most-once, but ordinary collection failure is
            # made inspectable instead of leaving only an ambiguous claim file.
            snapshot_limitations = ["collector failed after finish claim", "no semantic reviewer invoked"]
            try:
                failure_after = snapshot(self.root, packet.allowed_paths)
            except Exception:
                failure_after = current
                snapshot_limitations.append("post-error snapshot failed")
            value.update({"stage": "finished", "after": failure_after,
                          "actual_changed_paths": changed_paths(self.root, value["before"], failure_after),
                          "verification_mutations": changed_paths(self.root, current, failure_after),
                          "check": {"command_id": verification_command_id(packet),
                                    "status": "COLLECTOR_ERROR", "exit_code": None,
                                    "error_type": type(exc).__name__},
                          "change_contract_evidence": {}, "contract_findings": [],
                          "review_pipeline": "S0", "review_verdict": "INCONCLUSIVE",
                          "review_reasons": ["COLLECTOR_ERROR"], "review_exclusions": [],
                          "reviewer_invoked": False, "semantic_review_required": False,
                          "consumption": consumption("INCONCLUSIVE", self.review_policy.rollout_mode.value),
                          "execution_evidence_status": "INCONCLUSIVE",
                          "evaluation_hooks": evaluate_event_hooks(
                              pipeline=Pipeline.S0,
                              declared_paths=packet.allowed_paths,
                              actual_changed_paths=changed_paths(self.root, value["before"], failure_after),
                              before_identity=value["before"]["identity"],
                              after_identity=failure_after["identity"],
                              current_identity=failure_after["identity"],
                              evidence_status="INCONCLUSIVE",
                              verification_mutations=changed_paths(self.root, current, failure_after),
                              policy=self.review_policy.data,
                          ),
                          "lifecycle_binding": lifecycle_binding,
                          "limitations": snapshot_limitations})
            self._write(value)
        return self.read(receipt_path)
