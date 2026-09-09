"""Fail-closed, evidence-bearing native recovery after a classified OMP outage."""

from __future__ import annotations

import hashlib
import json
import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence


class NativeRecoveryRefused(ValueError):
    pass


INFRASTRUCTURE_FAILURES = frozenset({
    "OmpWorktreeError", "STALE_READ_HANDLE", "INVALID_READ_HANDLE",
    "PROPOSAL_TRANSPORT_FAILURE", "CHILD_DISPATCH_FAILURE", "SIDECAR_PROTOCOL_FAILURE",
})
_FORBIDDEN_NAMES = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
    "requirements.txt", "pyproject.toml", "poetry.lock", "pipfile", "pipfile.lock",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum", "composer.json", "composer.lock",
    ".env", "credentials", "auth", "security", "policy", "id_rsa", "id_ed25519",
})
_FORBIDDEN_PARTS = frozenset({"node_modules", ".git", "__pycache__", ".venv", "venv", "SteamLibrary", "Red Dead Redemption"})
_FORBIDDEN_PATH_TERMS = ("credential", "secret", "security", "policy", "auth")


def classify_infrastructure_failure(error: Any) -> str | None:
    text = str(error)
    for code in INFRASTRUCTURE_FAILURES:
        if code in text:
            return code
    lowered = text.lower()
    if "stale" in lowered and "handle" in lowered:
        return "STALE_READ_HANDLE"
    if "invalid" in lowered and "handle" in lowered:
        return "INVALID_READ_HANDLE"
    if "transport" in lowered and "proposal" in lowered:
        return "PROPOSAL_TRANSPORT_FAILURE"
    if "child" in lowered and "dispatch" in lowered:
        return "CHILD_DISPATCH_FAILURE"
    if "sidecar" in lowered and ("protocol" in lowered or "envelope" in lowered):
        return "SIDECAR_PROTOCOL_FAILURE"
    return None


def _paths(root: Path, values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 8:
        raise NativeRecoveryRefused("declared_paths must contain 1 to 8 paths")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or "\\" in value:
            raise NativeRecoveryRefused("declared path is not canonical")
        path = Path(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise NativeRecoveryRefused("declared path escapes the workspace")
        normalized = path.as_posix()
        if normalized != value or normalized in result:
            raise NativeRecoveryRefused("declared paths must be unique and canonical")
        lowered_parts = tuple(part.lower() for part in path.parts)
        if (any(part in _FORBIDDEN_PARTS for part in path.parts)
                or path.name.lower() in _FORBIDDEN_NAMES
                or any(term in part for part in lowered_parts for term in _FORBIDDEN_PATH_TERMS)):
            raise NativeRecoveryRefused("declared path is outside native recovery policy")
        result.append(normalized)
    return tuple(result)


def _hash(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise NativeRecoveryRefused("changed path is not a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_existing_targets(root: Path, paths: Sequence[str]) -> None:
    for relative in paths:
        target = root / relative
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise NativeRecoveryRefused("declared path is not a regular file")


def _windows_environment_value(name: str) -> str | None:
    """Read one Windows-owned path through WSL interop without pinning an identity."""
    command = shutil.which("cmd.exe")
    if command is None:
        return None
    completed = subprocess.run(
        [command, "/d", "/c", f"echo %{name}%"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value or value == f"%{name}%":
        return None
    return value


def _dotnet_validation_roots(dotnet: str) -> tuple[Path, str]:
    """Resolve host-compatible scratch and package paths without fixed machines."""
    configured_temp = os.environ.get("PRIME_DOTNET_TEMP")
    configured_packages = os.environ.get("NUGET_PACKAGES")
    if configured_temp and configured_packages:
        return Path(configured_temp).resolve(), configured_packages
    if dotnet.lower().endswith(".exe"):
        windows_temp = configured_temp or _windows_environment_value("TEMP")
        windows_profile = _windows_environment_value("USERPROFILE")
        converter = shutil.which("wslpath")
        if not windows_temp or not windows_profile or converter is None:
            raise NativeRecoveryRefused("Windows dotnet validation paths are unavailable")
        converted = subprocess.run(
            [converter, "-u", windows_temp],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if converted.returncode != 0 or not converted.stdout.strip():
            raise NativeRecoveryRefused("Windows dotnet validation root cannot be translated")
        packages = configured_packages or str(PureWindowsPath(windows_profile) / ".nuget" / "packages")
        return Path(converted.stdout.strip()).resolve(), packages
    return (
        Path(configured_temp or tempfile.gettempdir()).resolve(),
        configured_packages or str((Path.home() / ".nuget" / "packages").resolve()),
    )


def deterministic_recovery_validator(root: Path, paths: tuple[str, ...]) -> list[dict[str, Any]]:
    """Run bounded local syntax or focused tests for the declared file types."""
    _validate_existing_targets(root, paths)
    results: list[dict[str, Any]] = []
    suffixes = {Path(path).suffix.lower() for path in paths}
    if ".json" in suffixes:
        for relative in paths:
            if relative.lower().endswith(".json"):
                json.loads((root / relative).read_text(encoding="utf-8"))
        results.append({"status": "PASS", "deterministic": True, "command": "json-parse"})
    if ".yaml" in suffixes or ".yml" in suffixes:
        import yaml
        for relative in paths:
            if Path(relative).suffix.lower() in {".yaml", ".yml"}:
                yaml.safe_load((root / relative).read_text(encoding="utf-8"))
        results.append({"status": "PASS", "deterministic": True, "command": "yaml-parse"})
    if ".py" in suffixes:
        command = ["python3", "-m", "py_compile", *paths]
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                   timeout=120, check=False)
        results.append({"status": "PASS" if completed.returncode == 0 else "FAIL",
                        "deterministic": True, "command": command,
                        "exit_code": completed.returncode})
    if ".cs" in suffixes:
        test_source = next((root / relative for relative in paths
                            if "test" in relative.lower() and relative.lower().endswith(".cs")), None)
        project = None
        if test_source is not None:
            for parent in (test_source.parent, *test_source.parents):
                if parent == root.parent:
                    break
                project = next(parent.glob("*.csproj"), None)
                if project is not None:
                    break
        if project is None:
            raise NativeRecoveryRefused("focused C# test project is unavailable")
        dotnet = os.environ.get("PRIME_DOTNET_PATH") or shutil.which("dotnet") or shutil.which("dotnet.exe")
        if dotnet is None:
            raise NativeRecoveryRefused("focused C# validator is unavailable")
        temp_parent, nuget_packages = _dotnet_validation_roots(dotnet)
        if not temp_parent.is_dir():
            raise NativeRecoveryRefused("bounded C# validation root is unavailable")
        core_source = next((root / relative for relative in paths if not "test" in relative.lower()), None)
        core_project = None
        if core_source is not None:
            for parent in (core_source.parent, *core_source.parents):
                if parent == root.parent:
                    break
                core_project = next(parent.glob("*.csproj"), None)
                if core_project is not None:
                    break
        if core_project is None:
            raise NativeRecoveryRefused("focused C# source project is unavailable")
        for source_root in {project.parent, core_project.parent}:
            if any(candidate.is_symlink() for candidate in source_root.rglob("*")):
                raise NativeRecoveryRefused("C# validation source contains a symlink")
        with tempfile.TemporaryDirectory(prefix="prime-recovery-dotnet-", dir=temp_parent) as temporary:
            validation_root = Path(temporary)
            relative_project_dir = project.parent.relative_to(root)
            core_dir = core_project.parent
            shutil.copytree(project.parent, validation_root / relative_project_dir,
                            ignore=shutil.ignore_patterns("bin", "obj"))
            shutil.copytree(core_dir, validation_root / core_dir.relative_to(root),
                            ignore=shutil.ignore_patterns("bin", "obj"))
            if (root / "config").is_dir():
                shutil.copytree(root / "config", validation_root / "config")
            nuget_config = validation_root / "NuGet.Config"
            nuget_config.write_text(
                '<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
                '</packageSources></configuration>', encoding="utf-8",
            )
            relative_project = project.relative_to(root).as_posix()
            restore = [dotnet, "restore", relative_project, "--configfile", "NuGet.Config",
                       "--packages", nuget_packages]
            restored = subprocess.run(restore, cwd=validation_root, capture_output=True, text=True,
                                      timeout=180, check=False)
            results.append({"status": "PASS" if restored.returncode == 0 else "FAIL",
                            "deterministic": True, "command": restore, "network": "none",
                            "exit_code": restored.returncode,
                            "output": (restored.stdout + restored.stderr)[-4000:]})
            if restored.returncode == 0:
                test_filter = Path(test_source).stem if test_source is not None else ""
                command = [dotnet, "test", relative_project, "--no-restore", "--filter",
                           f"FullyQualifiedName~{test_filter}"]
                completed = subprocess.run(command, cwd=validation_root, capture_output=True,
                                           text=True, timeout=300, check=False)
                results.append({"status": "PASS" if completed.returncode == 0 else "FAIL",
                                "deterministic": True, "command": command, "network": "none",
                                "exit_code": completed.returncode,
                                "output": (completed.stdout + completed.stderr)[-4000:]})
    if not results:
        results.append({"status": "PASS", "deterministic": True, "command": "regular-file-static-check"})
    return results


def make_sidecar_recovery_executor(*, repo_root: Path, image: str, prompt: str,
                                   provider_id: str, model_id: str):
    """Build a read-only proposal adapter; this process is the only writer."""
    from prime_bridge.omp_sdk_runner import OmpSdkSidecarRunner
    runner = OmpSdkSidecarRunner.from_policy(repo_root, image)

    def executor(root: Path, paths: tuple[str, ...]) -> None:
        _validate_existing_targets(root, paths)
        async def request():
            return await runner.propose(prompt=prompt, project_root=root, model_id=model_id,
                                        provider_id=provider_id, allowed_paths=paths,
                                        cancellation=asyncio.Event())
        proposals, _evidence = asyncio.run(request())
        if not proposals or len(proposals) > len(paths):
            raise NativeRecoveryRefused("recovery proposal count is invalid")
        allowed = set(paths)
        seen: set[str] = set()
        for proposal in proposals:
            relative = proposal.relative_path
            if relative not in allowed or relative in seen:
                raise NativeRecoveryRefused("recovery proposal is outside or duplicates the allowlist")
            seen.add(relative)
            target = root / relative
            if proposal.operation == "replace":
                if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != proposal.expected_before_hash:
                    raise NativeRecoveryRefused("recovery proposal source hash is stale or unsafe")
            elif proposal.operation == "create":
                if target.exists() or proposal.expected_before_hash != hashlib.sha256(b"").hexdigest():
                    raise NativeRecoveryRefused("recovery create target is invalid")
            else:
                raise NativeRecoveryRefused("recovery proposal operation is invalid")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(proposal.proposed_bytes)

    return executor, deterministic_recovery_validator


def make_hermes_recovery_executor(*, prompt: str, provider_id: str, model_id: str,
                                  hermes_home: Path | None = None,
                                  command_runner: Callable[..., Any] = subprocess.run):
    """Build a no-tools recovery editor using the existing Codex subscription.

    Source bytes are supplied as data in one bounded prompt. The model cannot
    access the workspace; the host validates and writes its JSON proposals.
    """
    if (provider_id, model_id) != ("openai-codex", "gpt-5.4"):
        raise NativeRecoveryRefused("verified recovery requires openai-codex/gpt-5.4")
    home = Path(hermes_home or (Path.home() / ".hermes")).resolve()
    binary = home / "hermes-agent" / "venv" / "bin" / "hermes"
    if command_runner is subprocess.run and not binary.is_file():
        raise NativeRecoveryRefused("Hermes subscription client is unavailable")

    def executor(root: Path, paths: tuple[str, ...]) -> None:
        _validate_existing_targets(root, paths)
        sources: list[dict[str, Any]] = []
        for relative in paths:
            target = root / relative
            content = target.read_text(encoding="utf-8") if target.exists() else None
            sources.append({"path": relative, "content": content,
                            "before_hash": _hash(target)})
        request = {
            "task": prompt,
            "declared_paths": list(paths),
            "sources": sources,
            "response_contract": {
                "proposals": [{"path": "declared/path", "content": "complete replacement text"}],
            },
        }
        model_prompt = (
            "You are a bounded source recovery editor. Return one JSON object and no markdown. "
            "Do not call tools, browse, run commands, or request more context. Propose only files "
            "that must change, using exact declared paths and complete replacement text. Never "
            "delete a file. If no safe change is possible, return {\"proposals\":[]}. INPUT="
            + json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        )
        environment = {key: value for key, value in os.environ.items()
                       if key in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR"}}
        environment["HERMES_HOME"] = str(home)
        completed = command_runner(
            [str(binary), "--safe-mode", "--provider", provider_id, "-m", model_id,
             "-z", model_prompt],
            cwd=root, env=environment, capture_output=True, text=True, timeout=300, check=False,
        )
        if completed.returncode != 0:
            raise NativeRecoveryRefused("approved subscription recovery route failed")
        raw = completed.stdout.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1]).strip()
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NativeRecoveryRefused("subscription recovery response is not valid JSON") from exc
        proposals = response.get("proposals") if isinstance(response, dict) else None
        if not isinstance(proposals, list) or not proposals or len(proposals) > len(paths):
            raise NativeRecoveryRefused("subscription recovery returned no valid proposals")
        allowed = set(paths)
        seen: set[str] = set()
        for proposal in proposals:
            if not isinstance(proposal, dict) or set(proposal) != {"path", "content"}:
                raise NativeRecoveryRefused("subscription recovery proposal is malformed")
            relative, content = proposal["path"], proposal["content"]
            if relative not in allowed or relative in seen or not isinstance(content, str):
                raise NativeRecoveryRefused("subscription recovery proposal escapes the declared scope")
            seen.add(relative)
            target = root / relative
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise NativeRecoveryRefused("subscription recovery target is unsafe")
            if target.exists() and target.read_text(encoding="utf-8") == content:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="")

    return executor, deterministic_recovery_validator


def issue_verified_native_recovery(
    *,
    workspace: Path,
    declared_paths: Sequence[str],
    preflight: Mapping[str, Any],
    omp_error: Any,
    user_intent: bool,
    validation: Sequence[Mapping[str, Any]],
    rollback_status: str = "not-needed",
    requested_profile: str | None = None,
    resolved_provider: str | None = None,
    resolved_model: str | None = None,
    route_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Validate already-applied native edits and emit a durable fallback receipt.

    This function never edits files. The caller must perform only the explicitly
    declared, policy-eligible edit and then provide deterministic validation evidence.
    """
    if user_intent is not True:
        raise NativeRecoveryRefused("explicit verified-native-recovery intent is required")
    if preflight.get("status") != "READY" or preflight.get("omp_status") not in {"READY", None}:
        raise NativeRecoveryRefused("Prime preflight must be READY before native recovery")
    reason = classify_infrastructure_failure(omp_error)
    if reason is None:
        raise NativeRecoveryRefused("native recovery is refused for an unclassified OMP failure")
    root = Path(workspace).resolve()
    if not root.is_dir() or any(part in _FORBIDDEN_PARTS for part in root.parts):
        raise NativeRecoveryRefused("workspace is not an eligible code workspace")
    paths = _paths(root, declared_paths)
    if rollback_status not in {"not-needed", "completed", "verified"}:
        raise NativeRecoveryRefused("rollback status is not acceptable")
    if not validation or any(item.get("status") != "PASS" or not item.get("deterministic", False) for item in validation):
        raise NativeRecoveryRefused("deterministic validation evidence is required")
    before_after: list[dict[str, Any]] = []
    changed: list[str] = []
    for relative in paths:
        path = root / relative
        after = _hash(path)
        before = None
        if isinstance(validation[0].get("before_hashes"), Mapping):
            before = validation[0]["before_hashes"].get(relative)
        if before is not None and not isinstance(before, str):
            raise NativeRecoveryRefused("before hash is malformed")
        if before != after:
            if before is not None and after is None:
                raise NativeRecoveryRefused("native recovery cannot delete files")
            changed.append(relative)
        before_after.append({"path": relative, "before_hash": before, "after_hash": after})
    if not changed:
        raise NativeRecoveryRefused("native recovery contains no verified file changes")
    receipt = {
        "schema_version": "1.0", "outcome": "fallback", "reason": reason,
        "declared_paths": list(paths), "changed_paths": changed,
        "validation": [dict(item) for item in validation],
        "before_after_hashes": before_after, "rollback_status": rollback_status,
        "user_intent": True, "integration_authorized": False,
        "requested_profile": requested_profile, "resolved_provider": resolved_provider,
        "resolved_model": resolved_model, "route_fingerprint": route_fingerprint,
        "root_error": str(omp_error),
    }
    receipt["receipt_hash"] = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    return receipt


def execute_verified_recovery(
    *, workspace: Path, declared_paths: Sequence[str], preflight: Mapping[str, Any],
    omp_error: Any, user_intent: bool,
    executor: Callable[[Path, tuple[str, ...]], None],
    validator: Callable[[Path, tuple[str, ...]], Sequence[Mapping[str, Any]]],
    requested_profile: str | None = None, resolved_provider: str | None = None,
    resolved_model: str | None = None, route_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Run one user-authorized, allowlisted native recovery with rollback.

    The executor receives only the resolved workspace and validated paths. It
    cannot widen scope through this interface; validation failure restores the
    exact pre-execution regular-file snapshot before returning a blocked receipt.
    """
    root = Path(workspace).resolve()
    paths = _paths(root, declared_paths)
    if user_intent is not True or preflight.get("status") != "READY":
        raise NativeRecoveryRefused("verified recovery requires explicit intent and READY preflight")
    if classify_infrastructure_failure(omp_error) is None:
        raise NativeRecoveryRefused("verified recovery requires a classified infrastructure failure")
    _validate_existing_targets(root, paths)
    snapshot: dict[str, bytes | None] = {relative: (root / relative).read_bytes() if (root / relative).is_file() else None for relative in paths}
    before_hashes = {relative: (hashlib.sha256(value).hexdigest() if value is not None else None) for relative, value in snapshot.items()}
    validation: list[dict[str, Any]] = []
    try:
        executor(root, paths)
        validation = [dict(item) for item in validator(root, paths)]
        if not validation or any(item.get("status") != "PASS" or item.get("deterministic") is not True for item in validation):
            raise NativeRecoveryRefused("deterministic recovery validation failed")
        validation[0]["before_hashes"] = before_hashes
        return issue_verified_native_recovery(
            workspace=root, declared_paths=paths, preflight=preflight,
            omp_error=omp_error, user_intent=True, validation=validation,
            rollback_status="verified",
            requested_profile=requested_profile, resolved_provider=resolved_provider,
            resolved_model=resolved_model, route_fingerprint=route_fingerprint,
        )
    except Exception as exc:
        for relative, content in snapshot.items():
            target = root / relative
            if content is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        return {
            "outcome": "blocked", "reason": str(exc), "declared_paths": list(paths),
            "changed_paths": [], "validation": validation, "rollback_status": "completed",
            "root_error": str(omp_error), "recovery_error": str(exc),
            "before_hashes": before_hashes, "after_hashes": {
                relative: _hash(root / relative) for relative in paths
            },
            "requested_profile": requested_profile, "resolved_provider": resolved_provider,
            "resolved_model": resolved_model, "route_fingerprint": route_fingerprint,
        }
