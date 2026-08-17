#!/usr/bin/env bash
set -euo pipefail

COMMIT="95afd319a78ae017a41241d50b013d656a0685ce"
root="$(git rev-parse --show-toplevel)"
source_dir="$root/.git/phase01-gap-lifecycle/sources/prime-agent/$COMMIT"
output=""
while (($#)); do
  case "$1" in
    --source) source_dir="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) printf '%s\n' '{"status":"INCOMPATIBLE","reason":"unknown argument"}'; exit 2 ;;
  esac
done

python3 - "$source_dir" "$output" <<'PY'
import hashlib, json, os, stat, sys, tempfile
from pathlib import Path

source = Path(sys.argv[1])
output = Path(sys.argv[2]) if sys.argv[2] else None
expected = {
    "repository": "https://github.com/PrimeIntellect-ai/prime-agent.git",
    "version": "v0.7.1",
    "commit": "95afd319a78ae017a41241d50b013d656a0685ce",
    "tree": "caf6fa88a48d2a4b07b903c327ae3d6b2d1b0466",
    "package_lock_sha256": "39ee303bca10c0933cf917275613c8f44099f50de1650a5c356e7cda02b701e8",
}

def emit(status, reason, record=None, code=0):
    value = {"status": status, "reason": reason}
    if record is not None:
        value["record"] = record
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output and record is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, output)
    print(payload, end="")
    raise SystemExit(code)

if not source.is_dir() or source.is_symlink():
    emit("MISSING", "approved source cache not found")
try:
    marker = json.loads((source / ".prime-source.json").read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    emit("INCOMPATIBLE", "source marker is missing or invalid", code=2)
if marker != expected:
    emit("INCOMPATIBLE", "source marker identity mismatch", code=2)

members = []
for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
    relative = path.relative_to(source).as_posix()
    if path.is_symlink():
        emit("INCOMPATIBLE", f"symlink is forbidden: {relative}", code=2)
    info = path.lstat()
    if stat.S_ISDIR(info.st_mode):
        continue
    if not stat.S_ISREG(info.st_mode):
        emit("INCOMPATIBLE", f"non-regular member is forbidden: {relative}", code=2)
    if info.st_uid != os.getuid() or info.st_gid != os.getgid():
        emit("INCOMPATIBLE", f"unsafe ownership: {relative}", code=2)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        emit("INCOMPATIBLE", f"member changed while hashing: {relative}", code=2)
    members.append({
        "path": relative, "uid": before.st_uid, "gid": before.st_gid,
        "mode": stat.S_IMODE(before.st_mode), "device": before.st_dev,
        "inode": before.st_ino, "size": before.st_size, "mtime_ns": before.st_mtime_ns,
        "sha256": digest.hexdigest(),
    })

lock = next((item for item in members if item["path"] == "package-lock.json"), None)
if lock is None or lock["sha256"] != expected["package_lock_sha256"]:
    emit("INCOMPATIBLE", "package-lock identity mismatch", code=2)
root_info = source.stat()
record = {
    "schema_version": "1.0", **expected,
    "source_path": str(source.resolve()),
    "source_identity": {"uid": root_info.st_uid, "gid": root_info.st_gid, "mode": stat.S_IMODE(root_info.st_mode), "device": root_info.st_dev, "inode": root_info.st_ino, "mtime_ns": root_info.st_mtime_ns},
    "members": members,
    "consumer_reopen_contract": {
        "descriptor_lifetime": "process-local-only",
        "open_flags": ["O_RDONLY", "O_NOFOLLOW"],
        "compare_fields": ["uid", "gid", "mode", "device", "inode", "size", "mtime_ns", "sha256"],
        "requirements": ["reopen every member independently", "retain consumer-owned descriptors while consuming", "revalidate identity and hash before and after consumption"],
    },
}
emit("READY", "pinned source identity recorded without execution", record)
PY
