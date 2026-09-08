#!/usr/bin/env python3
"""Run the provider-free SDK clean-room qualification matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


V1_COMMIT = "04b7630937557c2dd4e6b429fc033873d5edf127"
HARNESSES = ("hermes", "opencode", "pi", "antigravity", "codex", "claude-code", "moa", "cursor")
SOURCE_VERIFIERS = (
    "tools/verify-v1-compatibility.py",
    "tools/verify-v2-provenance.py",
    "modules/orchestration-core/verify-core.py",
    "modules/adapters/hermes/verify-hermes-adapter.py",
    "modules/adapters/verify-adapters.py",
    "installer/verify-lifecycle.py",
)
HASH_RE = re.compile(r"^[0-9a-f]{40}$")


class QualificationError(RuntimeError):
    """A typed clean-room qualification failure."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def tree_digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        count += 1
    return digest.hexdigest(), count


def source_files(repository: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=repository, capture_output=True, check=True)
    names = sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})
    for name in names:
        path = repository / name
        if path.is_symlink() or not inside(path, repository):
            raise QualificationError("source contains an unsafe path")
    return [name for name in names if (repository / name).is_file()]


def source_digest(repository: Path) -> str:
    # Use Git's canonical bytes so CRLF checkout conventions do not look like
    # source changes. hash-object without -w reads only; it creates no objects.
    names = source_files(repository)
    result = subprocess.run(
        ["git", "hash-object", "--stdin-paths"], cwd=repository,
        input="".join(json.dumps(name, ensure_ascii=False) + "\n" for name in names),
        text=True, encoding="utf-8", capture_output=True, check=True,
    )
    hashes = result.stdout.splitlines()
    if not names or len(hashes) != len(names):
        raise QualificationError("empty or incomplete source fingerprint")
    digest = hashlib.sha256()
    for name, content_hash in zip(names, hashes):
        digest.update(name.encode("utf-8") + b"\0" + content_hash.encode("ascii") + b"\0")
    return digest.hexdigest()


def overlay_working_tree(repository: Path, clone: Path) -> None:
    names = source_files(repository)
    for name in source_files(clone):
        if name not in names:
            (clone / name).unlink()
    for name in names:
        target = clone / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / name, target)


def _safe_detail(value: str, roots: tuple[Path, ...]) -> str:
    detail = value.replace("\r", " ").replace("\n", " ")
    for root in roots:
        detail = detail.replace(str(root), "<clean-room>")
        detail = detail.replace(root.as_posix(), "<clean-room>")
    detail = re.sub(r"(?i)(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s]+|/(?:home|Users)/[^/\s]+)", "<private-path>", detail)
    detail = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<email>", detail)
    return detail[:500]


def run(command: list[str], cwd: Path, label: str, roots: tuple[Path, ...], timeout: int = 360) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode:
        first = (result.stdout or result.stderr or "no output").splitlines()[0]
        raise QualificationError(f"{label} failed (exit {result.returncode}): {_safe_detail(first, roots)}")
    return result


def run_json(command: list[str], cwd: Path, label: str, roots: tuple[Path, ...], timeout: int = 360) -> dict:
    result = run(command, cwd, label, roots, timeout)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise QualificationError(f"{label} returned invalid JSON") from exc
    if value.get("status") != "PASS":
        raise QualificationError(f"{label} returned {value.get('status', 'UNKNOWN')}")
    return value


def clone_at(repository: Path, destination: Path, commit: str, roots: tuple[Path, ...]) -> None:
    if not repository.is_dir() or not (repository / ".git").exists():
        raise QualificationError("repository must be a local non-bare Git checkout")
    run(
        ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(repository), str(destination)],
        repository,
        "local clone",
        roots,
    )
    run(["git", "checkout", "--quiet", "--detach", commit], destination, "detached checkout", roots)
    actual = run(["git", "rev-parse", "HEAD"], destination, "clone commit", roots).stdout.strip()
    if actual != commit:
        raise QualificationError("clean clone did not resolve the requested commit")
    if run(["git", "status", "--porcelain"], destination, "clone status", roots).stdout.strip():
        raise QualificationError("clean clone is dirty")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def qualify(repository: Path, commit: str, python: str, platform_label: str, work_root: Path | None = None, working_tree: bool = False) -> dict:
    repository = repository.resolve()
    if work_root is not None:
        work_root = work_root.resolve()
        if inside(work_root, repository):
            raise QualificationError("work root must be outside the source repository")
        if not work_root.is_dir():
            raise QualificationError("work root must already exist")
    if not HASH_RE.fullmatch(commit):
        raise QualificationError("commit must be a full 40-character lowercase Git identifier")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository,
        text=True, capture_output=True, check=False,
    )
    if status.returncode or (status.stdout.strip() and not working_tree):
        raise QualificationError("source repository must be clean")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        text=True, capture_output=True, check=False,
    )
    if head.returncode or head.stdout.strip() != commit:
        raise QualificationError("requested commit must equal the source checkout HEAD")

    source_hash = source_digest(repository)
    checks: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="lapart-clean-room-", dir=work_root) as temporary:
        root = Path(temporary).resolve()
        roots = (repository, root)
        clone = root / "v2-source"
        v1_source = root / "v1-source"
        clone_at(repository, clone, commit, roots)
        if working_tree:
            overlay_working_tree(repository, clone)
        if source_digest(clone) != source_hash:
            raise QualificationError("source changed during snapshot or checkout")
        checks.append({"name": "source-snapshot" if working_tree else "clean-local-clone", "status": "PASS"})

        run(["git", "merge-base", "--is-ancestor", V1_COMMIT, commit], clone, "v1 ancestry", roots)
        checks.append({"name": "v1-ancestry", "status": "PASS"})
        for verifier in SOURCE_VERIFIERS:
            run([python, verifier], clone, verifier, roots)
        checks.append({"name": "source-verifiers", "status": "PASS", "count": len(SOURCE_VERIFIERS)})

        payload_a, payload_b = root / "payload-a", root / "payload-b"
        build_a = run_json([python, "installer/kit.py", "build", "--output", str(payload_a), "--apply", "--yes"], clone, "payload build A", roots)
        build_b = run_json([python, "installer/kit.py", "build", "--output", str(payload_b), "--apply", "--yes"], clone, "payload build B", roots)
        payload_hash_a, payload_tree_files_a = tree_digest(payload_a)
        payload_hash_b, payload_tree_files_b = tree_digest(payload_b)
        if payload_hash_a != payload_hash_b or payload_tree_files_a != payload_tree_files_b:
            raise QualificationError("clean payload builds are not reproducible")
        payload_files = next(check["files"] for check in build_a["checks"] if check.get("name") == "payload")
        if payload_files != next(check["files"] for check in build_b["checks"] if check.get("name") == "payload"):
            raise QualificationError("clean payload file counts differ")
        checks.append({"name": "reproducible-payload", "status": "PASS", "files": payload_files})

        bundle_a, bundle_b = root / "bundle-a.zip", root / "bundle-b.zip"
        bundle_result_a = run_json([python, "installer/kit.py", "bundle", "--output", str(bundle_a), "--apply", "--yes"], clone, "bundle A", roots)
        bundle_result_b = run_json([python, "installer/kit.py", "bundle", "--output", str(bundle_b), "--apply", "--yes"], clone, "bundle B", roots)
        bundle_hash_a, bundle_hash_b = sha256_file(bundle_a), sha256_file(bundle_b)
        reported_a = next(check["sha256"] for check in bundle_result_a["checks"] if check.get("name") == "archive")
        reported_b = next(check["sha256"] for check in bundle_result_b["checks"] if check.get("name") == "archive")
        if len({bundle_hash_a, bundle_hash_b, reported_a, reported_b}) != 1:
            raise QualificationError("offline bundles are not reproducible")
        checks.append({"name": "reproducible-bundle", "status": "PASS"})

        install_root, data_root = root / "installed", root / "recipient-data"
        controller = payload_a / "installer" / "kit.py"
        run_json([python, str(controller), "install", "--target", str(install_root), "--data-dir", str(data_root), "--harness", "generic", "--apply", "--yes"], payload_a, "clean install", roots)
        installed_controller = install_root / "installer" / "kit.py"
        run_json([python, str(installed_controller), "doctor", "--target", str(install_root)], install_root, "installed doctor", roots)
        run_json([python, str(installed_controller), "verify", "--target", str(install_root)], install_root, "installed aggregate verify", roots)
        checks.append({"name": "install-doctor-verify", "status": "PASS"})

        for harness in HARNESSES:
            result = run_json([python, str(installed_controller), "connect", "--target", str(install_root), "--harness", harness, "--apply", "--yes"], install_root, f"connect {harness}", roots)
            activation = next(check for check in result["checks"] if check.get("name") == "activation")
            if activation.get("status") != "SKIPPED":
                raise QualificationError(f"{harness} connect changed activation state")
            verified = run_json([python, str(installed_controller), "verify", "--target", str(install_root), "--harness", harness], install_root, f"verify {harness}", roots)
            adapter = next(check for check in verified["checks"] if check.get("name") == f"adapter:{harness}")
            if adapter.get("provider_calls") != 0:
                raise QualificationError(f"{harness} verification used a provider")
        checks.append({"name": "all-harness-connect", "status": "PASS", "count": len(HARNESSES), "harnesses": list(HARNESSES)})

        clone_at(repository, v1_source, V1_COMMIT, roots)
        migration_root, migration_data = root / "migration-install", root / "migration-data"
        v1_controller = v1_source / "installer" / "kit.py"
        run_json([python, str(v1_controller), "install", "--target", str(migration_root), "--data-dir", str(migration_data), "--harness", "generic", "--apply", "--yes"], v1_source, "v1 install", roots)
        v1_manifest_hash = sha256_file(migration_root / "managed-files.json")
        sentinel = migration_data / "recipient-memory.txt"
        sentinel.write_text("recipient-owned\n", encoding="utf-8")
        run_json([python, str(controller), "upgrade", "--target", str(migration_root), "--apply", "--yes"], payload_a, "v1 to v2 upgrade", roots)
        migrated_controller = migration_root / "installer" / "kit.py"
        run_json([python, str(migrated_controller), "verify", "--target", str(migration_root)], migration_root, "migrated verify", roots)
        run_json([python, str(migrated_controller), "rollback", "--target", str(migration_root), "--apply", "--yes"], migration_root, "v2 to v1 rollback", roots)
        if sha256_file(migration_root / "managed-files.json") != v1_manifest_hash:
            raise QualificationError("v2 to v1 rollback did not restore the original manifest")
        if sentinel.read_text(encoding="utf-8") != "recipient-owned\n":
            raise QualificationError("migration changed recipient-owned data")
        run_json([python, str(v1_controller), "doctor", "--target", str(migration_root)], v1_source, "restored v1 doctor", roots)
        checks.append({"name": "v1-upgrade-rollback", "status": "PASS"})

        unmanaged = install_root / "recipient-note.txt"
        unmanaged.write_text("preserve\n", encoding="utf-8")
        data_sentinel = data_root / "recipient-memory.txt"
        data_sentinel.write_text("preserve\n", encoding="utf-8")
        run_json([python, str(installed_controller), "uninstall", "--target", str(install_root), "--apply", "--yes"], install_root, "managed uninstall", roots)
        if unmanaged.read_text(encoding="utf-8") != "preserve\n" or data_sentinel.read_text(encoding="utf-8") != "preserve\n":
            raise QualificationError("uninstall removed recipient-owned data")
        checks.append({"name": "managed-uninstall", "status": "PASS"})

        if source_digest(repository) != source_hash:
            raise QualificationError("source changed during qualification")
        return {
            "schema": "lapart.clean-room-platform.v2",
            "source_mode": "working-tree" if working_tree else "committed",
            "source_tree_sha256": source_hash,
            "decision": "CLEAN_ROOM_PASS",
            "platform": platform_label,
            "runtime": {"system": platform.system(), "python": platform.python_version()},
            "source_commit": commit,
            "v1_commit": V1_COMMIT,
            "checks": checks,
            "artifacts": {
                "payload_files": payload_files,
                "payload_tree_sha256": payload_hash_a,
                "bundle_sha256": bundle_hash_a,
                "release_manifest_sha256": sha256_file(payload_a / "RELEASE-MANIFEST.json"),
                "qualifier_sha256": sha256_file(clone / "tools/qualify-clean-room.py"),
            },
            "provider_calls": 0,
            "provider_tokens": 0,
            "pay_per_token": False,
            "service_restarts": 0,
            "live_activation": "unchanged",
        }


def selftest() -> dict:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="lapart-clean-room-selftest-") as temporary:
        root = Path(temporary)
        left, right = root / "left", root / "right"
        left.mkdir()
        right.mkdir()
        for directory in (left, right):
            (directory / "a.txt").write_text("one\n", encoding="utf-8")
            (directory / "b.txt").write_text("two\n", encoding="utf-8")
        if tree_digest(left) != tree_digest(right):
            failures.append("stable-tree-digest")
        (right / "b.txt").write_text("changed\n", encoding="utf-8")
        if tree_digest(left) == tree_digest(right):
            failures.append("tree-drift-detection")
    return {"cases": 2, "failures": failures}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--repository", type=Path)
    value.add_argument("--commit")
    value.add_argument("--python", default=sys.executable)
    value.add_argument("--platform-label")
    value.add_argument("--work-root", type=Path)
    value.add_argument("--output", type=Path)
    value.add_argument("--selftest", action="store_true")
    value.add_argument("--working-tree", action="store_true", help="Qualify an uncommitted snapshot for commit approval, never publication")
    return value


def main() -> int:
    args = parser().parse_args()
    if args.selftest:
        result = selftest()
        print(json.dumps(result, indent=2))
        return 0 if not result["failures"] else 1
    if not args.repository or not args.commit or not args.platform_label:
        parser().error("--repository, --commit, and --platform-label are required")
    if args.output and inside(args.output, args.repository):
        parser().error("--output must be outside the source repository")
    try:
        receipt = qualify(args.repository, args.commit, args.python, args.platform_label, args.work_root, args.working_tree)
    except (QualificationError, OSError, subprocess.SubprocessError) as exc:
        receipt = {
            "schema": "lapart.clean-room-platform.v1",
            "phase": 80,
            "decision": "HOLD",
            "platform": args.platform_label,
            "typed_failure": exc.__class__.__name__,
            "detail": _safe_detail(str(exc), (args.repository.resolve(),)),
            "provider_calls": 0,
            "provider_tokens": 0,
            "pay_per_token": False,
            "service_restarts": 0,
            "live_activation": "unchanged",
        }
        if args.output:
            _write_json(args.output, receipt)
        print(json.dumps(receipt, indent=2))
        return 1
    if args.output:
        _write_json(args.output, receipt)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
