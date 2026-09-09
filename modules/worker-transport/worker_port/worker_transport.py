"""Generated portable worker port. Host owns target, job ledger and permissions."""
from __future__ import annotations
import hashlib, json, os, re, subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from jsonschema import Draft202012Validator, FormatChecker
from .contracts import LedgerError, TERMINAL, validate_safe, utc_now
from .git_snapshot import EvidenceError, snapshot, refuse_git_filters
ROOT = Path(__file__).resolve().parent

BINDING_IDENTITY_FIELDS = (
    "job_id", "task_id", "goal_id", "contract_sha256", "harness", "session_id",
    "target_id", "target_policy_sha256", "repository", "declared_paths",
    "route_fingerprint", "runtime_fingerprint",
)


class TransportError(LedgerError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def binding_digest(value: dict) -> str:
    """Keep preparation and persisted-receipt validation on the same identity."""
    return digest({field: value[field] for field in BINDING_IDENTITY_FIELDS})


def validate(name: str, value: dict) -> None:
    if name not in {"remote-worktree-receipt", "external-status-event"}:
        raise TransportError("unknown contract")
    validate_safe(value, max_bytes=16384)
    schema = json.loads((ROOT / "schemas" / (name + ".schema.json")).read_text())
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        # Do not echo untrusted event values (possibly credential-shaped).
        raise TransportError("invalid " + name + " contract")


def git(repo: Path, *args: str, binary: bool = False):
    try:
        refuse_git_filters(repo)
    except EvidenceError as exc:
        raise TransportError(str(exc)) from exc
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false",
         "-C", str(repo), *args], capture_output=True, text=not binary, timeout=60,
    )
    if result.returncode:
        raise TransportError("Git operation failed: " + args[0])
    return result.stdout if binary else result.stdout.strip()


def _scoped(root: Path, relative: str) -> Path:
    value = PurePosixPath(relative)
    if tuple(part.casefold() for part in value.parts[:2]) == (".planning", "review-evidence"):
        raise TransportError("reserved review evidence path")
    if (not relative or "\\" in relative or ":" in relative or value.is_absolute()
            or any(part in {"..", ".git"} for part in value.parts)
            or value.as_posix() != relative or relative == "."):
        raise TransportError("unsafe declared path")
    candidate = root / relative
    for parent in (candidate, *candidate.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise TransportError("symlink worktree path forbidden")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise TransportError("path outside worktree")
    return candidate


def _worker_snapshot(root: Path, declared: list[str]) -> dict:
    """Include effective worktree modes in worker evidence identity."""
    state = snapshot(root, tuple(declared))
    filemode = git(root, "config", "--bool", "core.filemode") == "true"
    modes = {}
    for name in declared:
        target = _scoped(root, name)
        try:
            mode = "100755" if target.stat().st_mode & 0o100 else "100644"
        except FileNotFoundError:
            mode = None
        if mode is not None and not filemode:
            mode = state["index"].get(name, "100644").split(":", 1)[0]
        modes[name] = mode
    state["worktree_modes"] = modes
    state["filemode"] = filemode
    state["identity"] = digest({"snapshot":state["identity"], "modes":modes, "filemode":filemode})
    return state


def _captured_patch(root: Path, declared: list[str], state: dict) -> tuple[bytes, dict, set[str]]:
    """Bind each full-index patch to the exact bytes used for its SHA256.

    Per-path literal diffs avoid parsing quoted/unusual Git filenames. Untracked
    files remain hash-only evidence; this receipt never authorizes an import.
    No hash-object, index writes, filters, textconv, or external diff are used.
    """
    algorithm = git(root, "rev-parse", "--show-object-format")
    if algorithm not in {"sha1", "sha256"}:
        raise TransportError("unsupported capture object format")
    zero = "0" * (40 if algorithm == "sha1" else 64)
    chunks = []
    captured_paths = set()
    modes = {}
    filemode = git(root, "config", "--bool", "core.filemode") == "true"
    for name in sorted(declared):
        target = _scoped(root, name)
        try:
            with target.open("rb") as stream:
                data = stream.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:raise TransportError("capture byte budget exceeded")
        except FileNotFoundError:
            data = None
        sha = hashlib.sha256(data).hexdigest() if data is not None else "ABSENT"
        if sha != state["contents"].get(name):
            raise TransportError("worktree changed during collection: capture mismatch")
        oid = hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() if data is not None else zero
        base = git(root, "ls-tree", "-z", "HEAD", "--", ":(literal)" + name, binary=True)
        base_oid = base.split(b"\t", 1)[0].split()[-1].decode() if base else zero
        base_mode = base.split(b" ", 1)[0].decode() if base else "100644"
        mode = ("100755" if target.stat().st_mode & 0o100 else "100644") if data is not None else None
        if data is not None and not filemode:
            mode = state["index"].get(name, base_mode).split(":", 1)[0]
        if mode != state["worktree_modes"][name] or filemode != state["filemode"]:
            raise TransportError("worktree mode changed during capture")
        modes[name] = mode
        part = git(root, "diff", "--binary", "--full-index", "--no-renames", "--no-ext-diff", "--no-textconv",
                   "--no-color", "HEAD", "--", ":(literal)" + name, binary=True)
        # Only headers, never hunk text (which may itself contain 'index ...').
        header = re.split(rb"(?m)^(?:@@|GIT binary patch)", part, maxsplit=1)[0]
        indices = re.findall(rb"(?m)^index ([0-9a-f]+)\.\.([0-9a-f]+)(?: [0-7]+)?$", header)
        if len(indices) > 1 or (indices and indices[0] != (base_oid.encode(), oid.encode())):
            raise TransportError("patch blob differs from captured bytes")
        if not indices and base and oid != base_oid:
            raise TransportError("patch omits captured content change")
        if part and not indices and not re.search(rb"(?m)^old mode [0-7]+$", header):
            raise TransportError("unsupported patch capture headers")
        mode_headers = re.findall(rb"(?m)^(?:new mode|new file mode) ([0-7]+)$", header)
        patch_mode = mode_headers[0].decode() if mode_headers else base_mode
        if (part or base) and (len(mode_headers)>1 or (data is not None and patch_mode!=mode)
                     or (data is None and not re.search(rb"(?m)^deleted file mode [0-7]+$",header))):
            raise TransportError("patch mode differs from capture")
        chunks.append(part)
        if part or (not base and data is not None):
            captured_paths.add(name)
    return b"".join(chunks), modes, captured_paths


class WorkerTransport:
    """Bindings and dedupe live in the existing job DB, never in a second ledger.

    The caller is the host owner, not an external event. This API deliberately
    cannot start jobs or assert capability approval. Existing broker dispatch
    must authorize any later execution and verify/import changes independently.
    """
    def __init__(self, ledger, *, target: dict, external_source: str, enabled: bool = True, external_enabled: bool = True):
        self.ledger, self.enabled, self.external_enabled = ledger, enabled, external_enabled
        # This constructor is a trusted host port, never an event field.
        if (not isinstance(target, dict) or set(target) != {"target_id", "policy_sha256"}
                or not isinstance(target["target_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", target["target_id"])
                or not isinstance(target["policy_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", target["policy_sha256"])
                or not isinstance(external_source, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", external_source)):
            raise TransportError("invalid host target binding")
        if (not Path(ledger.path).is_file() or not ledger.allowed_roots
                or any(not callable(getattr(ledger, name, None)) for name in ("get", "_connect", "_event", "_name"))):
            raise TransportError("existing compatible owner ledger required")
        self.target = dict(target)
        self.target["policy_sha256"] = digest({"target": target, "external_source": external_source})
        self.external_source = external_source
        with ledger._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS worker_bindings (
                    binding_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL, lifecycle TEXT NOT NULL,
                    external_run_id TEXT UNIQUE,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
                CREATE TABLE IF NOT EXISTS worker_observations (
                    event_id TEXT PRIMARY KEY, binding_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, payload_sha256 TEXT NOT NULL,
                    event_json TEXT NOT NULL, received_at TEXT NOT NULL,
                    UNIQUE(binding_id, sequence),
                    FOREIGN KEY(binding_id) REFERENCES worker_bindings(binding_id)
                );
            """)

    def _enabled(self):
        if not self.enabled:
            raise TransportError("worker transport disabled")

    def _binding(self, binding_id: str) -> tuple[dict, dict]:
        with self.ledger._connect() as db:
            row = db.execute("SELECT * FROM worker_bindings WHERE binding_id=?", (binding_id,)).fetchone()
        if row is None:
            raise TransportError("unknown worker binding")
        receipt = json.loads(row["receipt_json"])
        validate("remote-worktree-receipt", receipt)
        if (receipt["binding_id"] != binding_id or binding_digest(receipt) != binding_id
                or receipt["job_id"] != row["job_id"]):
            raise TransportError("worker binding integrity mismatch")
        job = self.ledger.get(receipt["job_id"])
        if (str(Path(job["workspace"]).resolve()) != receipt["repository"]
                or receipt["target_id"] != self.target["target_id"]
                or receipt["target_policy_sha256"] != self.target["policy_sha256"]
                or job["route"]["fingerprint"] != receipt["route_fingerprint"]
                or job["runtime"]["fingerprint"] != receipt["runtime_fingerprint"]
                or job["session_id"] != receipt["session_id"]):
            raise TransportError("canonical job identity changed")
        return receipt, dict(row)

    def prepare(self, *, job_id: str, task_id: str, goal_id: str | None,
                contract_sha256: str, harness: str, declared_paths: list[str],
                worktree_root: Path) -> dict:
        self._enabled()
        job = self.ledger.get(job_id)
        repo = Path(job["workspace"]).resolve(strict=True)
        root = worktree_root.resolve(strict=True)
        if (worktree_root.is_symlink() or root.is_relative_to(repo)
                or not any(root.is_relative_to(p) for p in self.ledger.allowed_roots)):
            raise TransportError("worktree root must be outside source and inside allowed roots")
        for path in declared_paths:
            _scoped(repo, path)
        if git(repo, "rev-parse", "--show-toplevel") != str(repo).replace("\\", "/"):
            # Windows Git prints slash paths; resolve before comparing.
            if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
                raise TransportError("workspace must be repository root")
        identity = dict(job_id=job_id, task_id=task_id, goal_id=goal_id,
                        contract_sha256=contract_sha256, harness=harness,
                        session_id=job["session_id"], target_id=self.target["target_id"],
                        target_policy_sha256=self.target["policy_sha256"],
                        repository=str(repo), declared_paths=sorted(declared_paths),
                        route_fingerprint=job["route"]["fingerprint"],
                        runtime_fingerprint=job["runtime"]["fingerprint"])
        binding_id = binding_digest(identity)
        worktree = root / binding_id
        with self.ledger._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT binding_id FROM worker_bindings WHERE job_id=?", (job_id,)).fetchone()
            if existing:
                if existing[0] != binding_id:
                    raise TransportError("job already bound to a different task contract")
                db.commit()
                receipt, _ = self._binding(binding_id)
                if Path(receipt["worktree"]).parent != root:
                    raise TransportError("worktree location changed")
                return self.reconnect(binding_id)
            if job["status"] in TERMINAL:
                raise TransportError("cannot prepare a terminal job")
            if git(repo, "status", "--porcelain", "--untracked-files=all"):
                raise TransportError("dirty source requires explicit snapshot; no implicit copying")
            if worktree.exists():
                raise TransportError("unowned worktree path already exists")
            receipt = dict(schema_version="1.0", binding_id=binding_id, **identity,
                           base_commit=git(repo, "rev-parse", "HEAD"),
                           worktree=str(worktree), created_at=utc_now())
            validate("remote-worktree-receipt", receipt)
            db.execute("INSERT INTO worker_bindings VALUES(?,?,?,'preparing',NULL)",
                       (binding_id, job_id, json.dumps(receipt, sort_keys=True)))
            self.ledger._event(db, job_id, "worker_reserved", {"binding_id": binding_id})
            db.commit()  # Claim BEFORE side effects. Ambiguous failure never relaunches.
        try:
            git(repo, "worktree", "add", "--detach", str(worktree), receipt["base_commit"])
            self._verify_worktree(receipt)
        except Exception:
            self._lifecycle(binding_id, "needs-reconciliation")
            raise
        self._lifecycle(binding_id, "ready")
        return self.reconnect(binding_id)

    def _verify_worktree(self, receipt: dict) -> None:
        repo, worktree = Path(receipt["repository"]), Path(receipt["worktree"])
        for declared in receipt["declared_paths"]:
            _scoped(worktree, declared)
        if (worktree == repo or worktree.name != receipt["binding_id"]
                or not any(worktree.resolve().is_relative_to(p) for p in self.ledger.allowed_roots)):
            raise TransportError("worktree location identity mismatch")
        if worktree.is_symlink() or not worktree.is_dir():
            raise TransportError("worktree missing or redirected; do not relaunch")
        if Path(git(worktree, "rev-parse", "--show-toplevel")).resolve() != worktree.resolve():
            raise TransportError("worktree root identity mismatch")
        common = lambda p: Path(git(p, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        if common(repo) != common(worktree):
            raise TransportError("worktree repository identity mismatch")
        if git(worktree, "rev-parse", "HEAD") != receipt["base_commit"]:
            raise TransportError("worktree HEAD changed; host reconciliation required")

    def _lifecycle(self, binding_id: str, state: str) -> None:
        with self.ledger._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT job_id FROM worker_bindings WHERE binding_id=?", (binding_id,)).fetchone()
            if not row:
                raise TransportError("unknown worker binding")
            db.execute("UPDATE worker_bindings SET lifecycle=? WHERE binding_id=?", (state, binding_id))
            self.ledger._event(db, row[0], "worker_lifecycle", {"binding_id": binding_id, "state": state})
            db.commit()

    def reconnect(self, binding_id: str) -> dict:
        self._enabled()
        receipt, row = self._binding(binding_id)
        self._verify_worktree(receipt)
        return {"receipt": receipt, "lifecycle": row["lifecycle"],
                "canonical_status": self.ledger.get(receipt["job_id"])["status"],
                "external_run_id": row["external_run_id"], "launch_permitted": False}

    def reconcile(self, binding_id: str) -> dict:
        """Recover a completed Git side effect after a lost reply, never redo it."""
        result = self.reconnect(binding_id)
        self.collect(binding_id)
        if result["lifecycle"] in {"preparing", "needs-reconciliation"}:
            self._lifecycle(binding_id, "ready")
        return self.reconnect(binding_id)

    def collect(self, binding_id: str) -> dict:
        self._enabled()
        receipt, _ = self._binding(binding_id)
        self._verify_worktree(receipt)
        root = Path(receipt["worktree"])
        for declared in receipt["declared_paths"]:
            _scoped(root, declared)
        try:
            state = _worker_snapshot(root, receipt["declared_paths"])
        except EvidenceError as exc:
            raise TransportError("canonical evidence snapshot refused worktree") from exc
        paths = set(filter(None, git(root, "diff", "--name-only", "--no-ext-diff", "--no-textconv", "--no-renames", "-z", "HEAD", "--", binary=True).decode().split("\0")))
        paths.update(filter(None, git(root, "ls-files", "--others", "--exclude-standard", "-z", binary=True).decode().split("\0")))
        if paths - set(receipt["declared_paths"]):
            raise TransportError("out-of-scope changes; preserve and reconcile")
        for path in paths:
            _scoped(root, path)
        if set(state["contents"]) - set(receipt["declared_paths"]):
            raise TransportError("out-of-scope changes appeared during collection")
        paths.update(name for name in receipt["declared_paths"]
                     if name not in state["index"] and state["contents"][name] != "ABSENT")
        patch, modes, captured_paths = _captured_patch(root, receipt["declared_paths"], state)
        paths = captured_paths
        files = {path: None if state["contents"][path] == "ABSENT" else state["contents"][path]
                 for path in sorted(paths)}
        try:
            after = _worker_snapshot(root, receipt["declared_paths"])
        except EvidenceError as exc:
            raise TransportError("worktree changed during collection") from exc
        self._verify_worktree(receipt)
        if after["identity"] != state["identity"]:
            raise TransportError("worktree changed during collection")
        return {"binding_id": binding_id, "base_commit": receipt["base_commit"],
                "files": files, "file_modes": {name:modes[name] for name in files},
                "tracked_patch_sha256": hashlib.sha256(patch).hexdigest(),
                "snapshot_sha256": state["identity"], "collected_at": utc_now(),
                "tests_verified": False, "review_verdict": "not_evaluated",
                "import_authorized": False}

    def preserve(self, binding_id: str) -> dict:
        self._enabled()
        receipt, _ = self._binding(binding_id)
        self._lifecycle(binding_id, "preserved")
        return {"worktree": receipt["worktree"], "removed": False, "merged": False}

    def bind_external(self, binding_id: str, external_run_id: str) -> None:
        """Host-only linkage; an event cannot register or rebind itself."""
        self._enabled()
        if not self.external_enabled:
            raise TransportError("External observation disabled")
        self.ledger._name("external_run_id", external_run_id)
        receipt, _ = self._binding(binding_id)
        with self.ledger._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT external_run_id FROM worker_bindings WHERE binding_id=?", (binding_id,)).fetchone()
            if row[0] not in (None, external_run_id):
                raise TransportError("External run already bound")
            db.execute("UPDATE worker_bindings SET external_run_id=? WHERE binding_id=?", (external_run_id, binding_id))
            self.ledger._event(db, receipt["job_id"], "external_bound", {"binding_id": binding_id, "external_run_id": external_run_id})
            db.commit()

    def observe_external(self, event: dict, *, now: datetime | None = None) -> dict:
        self._enabled()
        if not self.external_enabled:
            raise TransportError("External observation disabled")
        validate("external-status-event", event)
        if event["source"] != self.external_source:
            raise TransportError("external source identity mismatch")
        receipt, row = self._binding(event["binding_id"])
        if any(event[k] != receipt[k] for k in ("job_id", "harness", "session_id")) or event["external_run_id"] != row["external_run_id"]:
            raise TransportError("External event identity mismatch")
        fingerprint = digest(event)
        observed = datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
        clock = now or datetime.now(timezone.utc)
        with self.ledger._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT payload_sha256 FROM worker_observations WHERE event_id=?", (event["event_id"],)).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise TransportError("duplicate event identity has different content")
                return {"status": "duplicate", "canonical_status_changed": False}
            if not -30 <= (clock - observed).total_seconds() <= 300:
                raise TransportError("stale or future External observation")
            latest = db.execute("SELECT sequence,event_json FROM worker_observations WHERE binding_id=? ORDER BY sequence DESC LIMIT 1", (event["binding_id"],)).fetchone()
            if latest and event["sequence"] <= latest[0]:
                raise TransportError("out-of-order External observation")
            if latest:
                prior = json.loads(latest[1])
                prior_time = datetime.fromisoformat(prior["observed_at"].replace("Z", "+00:00"))
                if observed < prior_time:
                    raise TransportError("out-of-order External observation time")
                if prior["status"] in {"done", "failed", "cancelled"}:
                    raise TransportError("terminal external run cannot regress or reopen")
                if prior["status"] != "queued" and event["status"] == "queued":
                    raise TransportError("external run cannot regress to queued")
            db.execute("INSERT INTO worker_observations VALUES(?,?,?,?,?,?)", (event["event_id"], event["binding_id"], event["sequence"], fingerprint, json.dumps(event, sort_keys=True), utc_now()))
            self.ledger._event(db, receipt["job_id"], "external_observation", {"event_id": event["event_id"], "binding_id": event["binding_id"], "reported_status": event["status"], "verified": False})
            db.commit()
        return {"status": "recorded", "reported_status": event["status"],
                "canonical_status_changed": False, "review_triggered": False}
