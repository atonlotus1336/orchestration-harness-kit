#!/usr/bin/env python3
"""Install one checksum-pinned Herdr release without activating the evaluator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform as host_platform
import shutil
import stat
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.9.0"
TAG = "v0.9.0"
RELEASE_COMMIT = "b99002ac99b09e00b4ca692436cb15a6b0d676f1"
EXPECTED_VERSION = "herdr 0.9.0"
RELEASE_BASE = f"https://github.com/herdrdev/herdr/releases/download/{TAG}"
ASSETS = {
    "windows-x86_64": {
        "name": "herdr-windows-x86_64.zip",
        "sha256": "b4508c445de1c1a68c760a01735da2aba2fa214b2aafd4b07f732e49b2a64b11",
        "archive": "zip",
    },
    "linux-x86_64": {
        "name": "herdr-linux-x86_64",
        "sha256": "4fa1a01158dd8043da92d31b270780b0dcc10603038d9b61cac4d81ab63fb71f",
        "archive": "raw",
    },
    "linux-aarch64": {
        "name": "herdr-linux-aarch64",
        "sha256": "9c8db20fb7e7427b138d5367113f1621ffd319f2f65d6f009e2594029115f0d2",
        "archive": "raw",
    },
    "macos-x86_64": {
        "name": "herdr-macos-x86_64",
        "sha256": "d0c920b2a126a74809fa1491411c9a097a44786cac9c2ca51b818a995581cf16",
        "archive": "raw",
    },
    "macos-aarch64": {
        "name": "herdr-macos-aarch64",
        "sha256": "32b53df09872628059c789a69f02a6b8e29e14ddf26711421f3463f70c1aef17",
        "archive": "raw",
    },
}


class BootstrapError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def platform_key() -> str:
    system = host_platform.system().lower()
    machine = host_platform.machine().lower()
    os_name = {"windows": "windows", "linux": "linux", "darwin": "macos"}.get(system)
    arch = {
        "amd64": "x86_64", "x86_64": "x86_64",
        "arm64": "aarch64", "aarch64": "aarch64",
    }.get(machine)
    key = f"{os_name}-{arch}"
    if os_name is None or arch is None or key not in ASSETS:
        raise BootstrapError(f"unsupported Herdr bootstrap platform: {system}/{machine}")
    return key


def safe_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == Path(resolved.anchor) or len(resolved.parts) < 2:
        raise BootstrapError(f"unsafe install root: {resolved}")
    return resolved


def asset(platform_name: str) -> dict[str, str]:
    if platform_name not in ASSETS:
        raise BootstrapError(f"unsupported Herdr bootstrap target: {platform_name}")
    selected = dict(ASSETS[platform_name])
    selected["url"] = f"{RELEASE_BASE}/{selected['name']}"
    return selected


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "LapArt-Herdr-bootstrap/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        final_host = urllib.parse.urlparse(response.geturl()).hostname
        if final_host not in {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}:
            raise BootstrapError("Herdr release redirect left the approved GitHub hosts")
        shutil.copyfileobj(response, output, length=1024 * 1024)


def extract_windows(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            relative = Path(member.filename.replace("\\", "/"))
            mode = member.external_attr >> 16
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or stat.S_ISLNK(mode)
                or not relative.parts
            ):
                raise BootstrapError(f"unsafe path in Herdr archive: {member.filename}")
            target = destination / relative
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    candidates = list(destination.rglob("herdr.exe"))
    if len(candidates) != 1:
        raise BootstrapError("Herdr Windows archive must contain exactly one herdr.exe")
    return candidates[0]


def run_version(executable: Path) -> str:
    command = [str(executable), "--version"]
    result = subprocess.run(command, capture_output=True, timeout=20, check=False)
    output = (result.stdout + result.stderr).decode("utf-8", errors="strict").strip()
    if result.returncode != 0 or output != EXPECTED_VERSION:
        raise BootstrapError("installed Herdr binary failed the pinned version check")
    return output


def install(platform_name: str, install_root: Path, artifact: Path | None) -> dict:
    selected = asset(platform_name)
    root = safe_root(install_root)
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="herdr-bootstrap-", dir=root.parent) as folder:
        temporary = Path(folder)
        payload = temporary / selected["name"]
        if artifact is None:
            download(selected["url"], payload)
        else:
            source = artifact.expanduser().resolve(strict=True)
            shutil.copy2(source, payload)
        observed = digest(payload)
        if observed != selected["sha256"]:
            raise BootstrapError(
                f"Herdr artifact checksum mismatch: expected {selected['sha256']}, observed {observed}"
            )

        staged = temporary / "staged"
        staged.mkdir()
        if selected["archive"] == "zip":
            extracted = temporary / "extracted"
            extracted.mkdir()
            binary = extract_windows(payload, extracted)
            for item in binary.parent.iterdir():
                target = staged / item.name
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
            staged_binary = staged / "herdr.exe"
        else:
            staged_binary = staged / selected["name"]
            shutil.copy2(payload, staged_binary)
            staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IXUSR)

        run_version(staged_binary)
        target = root / (".lapart-herdr" if platform_name == "windows-x86_64" else f".local/{platform_name}/{VERSION}")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = root / f".bootstrap-backup-{uuid.uuid4().hex}"
        had_target = target.exists()
        try:
            if had_target:
                os.replace(target, backup)
            os.replace(staged, target)
            installed_binary = target / staged_binary.name
            if platform_name == "windows-x86_64":
                launcher = root / "herdr.cmd"
                launcher.write_text(
                    '@echo off\nsetlocal\nset "HERDR_ROOT=%~dp0"\n'
                    '"%HERDR_ROOT%.lapart-herdr\\herdr.exe" %*\n',
                    encoding="utf-8", newline="\r\n",
                )
            else:
                launcher = installed_binary
            version = run_version(installed_binary)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            if had_target and backup.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return {
            "status": "PASS",
            "release": VERSION,
            "release_commit": RELEASE_COMMIT,
            "platform": platform_name,
            "artifact": selected["name"],
            "artifact_sha256": observed,
            "executable": str(installed_binary),
            "launcher": str(launcher),
            "version_output": version,
            "activation_changed": False,
        }


def status(platform_name: str, install_root: Path) -> dict:
    selected = asset(platform_name)
    root = safe_root(install_root)
    binary = (
        root / ".lapart-herdr/herdr.exe"
        if platform_name == "windows-x86_64"
        else root / f".local/{platform_name}/{VERSION}/{selected['name']}"
    )
    value = {
        "status": "INSTALLED" if binary.is_file() else "ABSENT",
        "release": VERSION,
        "release_commit": RELEASE_COMMIT,
        "platform": platform_name,
        "artifact": selected["name"],
        "artifact_sha256": selected["sha256"],
        "executable": str(binary),
        "activation_changed": False,
    }
    if binary.is_file():
        value["version_output"] = run_version(binary)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("bootstrap", "status"))
    parser.add_argument("--platform", choices=sorted(ASSETS), default=None)
    parser.add_argument("--install-root", type=Path, default=ROOT.parent / "herdr")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    selected_platform = args.platform or platform_key()
    if args.command == "status":
        print(json.dumps(status(selected_platform, args.install_root), sort_keys=True))
        return 0
    selected = asset(selected_platform)
    if not args.apply:
        print(json.dumps({
            "status": "DRY-RUN", "release": VERSION,
            "release_commit": RELEASE_COMMIT, "platform": selected_platform,
            "artifact": selected["name"], "artifact_sha256": selected["sha256"],
            "install_root": str(safe_root(args.install_root)),
            "activation_changed": False,
        }, sort_keys=True))
        return 0
    if not args.yes:
        raise SystemExit("BLOCKED: --apply requires --yes")
    try:
        print(json.dumps(install(selected_platform, args.install_root, args.artifact), sort_keys=True))
        return 0
    except (BootstrapError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
