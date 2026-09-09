#!/usr/bin/env python3
"""Verify and restore the private Hermes system snapshot.

Dry-run is the default. Use --apply to write missing files and --overwrite only
when intentionally updating an existing installation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path


DESTINATIONS = {
    "hermes-agent-self-evolution": Path("hermes-agent-self-evolution"),
    "autoexperiment": Path("scripts") / "autoexperiment",
    "ops": Path("scripts") / "ops",
    "skills-shared": Path("skills-shared"),
    "workspace-support": Path("."),
}


def sha256(path: Path) -> str:
    """Hash the exact recipient bytes recorded by the snapshot exporter."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_wsl_path(path: Path) -> str:
    if path.drive:
        drive = path.drive[0].lower()
        tail = "/".join(path.parts[1:])
        return f"/mnt/{drive}/{tail}"
    return path.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--substitutions", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    snapshot = repo_root / "system-snapshot"
    manifest_path = snapshot / "SNAPSHOT-MANIFEST.json"
    if not manifest_path.is_file():
        raise SystemExit(f"BLOCKED: snapshot manifest missing: {manifest_path}")

    temp_context = None
    if args.selftest:
        temp_context = tempfile.TemporaryDirectory(prefix="orchestration-snapshot-")
        workspace = (Path(temp_context.name) / "workspace").resolve()
        args.apply = True
    elif args.workspace_root:
        workspace = args.workspace_root.resolve()
    else:
        raise SystemExit("BLOCKED: --workspace-root is required unless --selftest is used")
    if workspace == workspace.anchor or len(workspace.parts) < 2:
        raise SystemExit(f"BLOCKED: unsafe workspace root: {workspace}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    substitutions: dict[str, str] = {
        "__WINDOWS_WORKSPACE__": str(workspace),
        "__WSL_WORKSPACE__": to_wsl_path(workspace),
    }
    if args.substitutions:
        substitutions.update(json.loads(args.substitutions.read_text(encoding="utf-8-sig")))
    required_placeholders = set(manifest.get("private_placeholders", []))
    if args.selftest:
        substitutions.update({
            placeholder: f"selftest-value-{index:02d}"
            for index, placeholder in enumerate(sorted(required_placeholders), start=1)
        })
    missing_substitutions = sorted(
        placeholder for placeholder in required_placeholders
        if not substitutions.get(placeholder)
        or str(substitutions[placeholder]).startswith("<")
    )
    corrupt: list[str] = []
    conflicts: list[str] = []
    pending: list[tuple[Path, Path]] = []

    for item in manifest["files"]:
        relative = Path(item["path"])
        group = relative.parts[0]
        if group not in DESTINATIONS:
            corrupt.append(f"unknown snapshot group: {group}")
            continue
        source = snapshot / relative
        if not source.is_file() or sha256(source) != item["sha256"]:
            corrupt.append(relative.as_posix())
            continue
        restored_path = item["path"]
        for placeholder, value in substitutions.items():
            restored_path = restored_path.replace(placeholder, str(value))
        restored_relative = Path(restored_path)
        if (
            restored_relative.is_absolute()
            or ".." in restored_relative.parts
            or not restored_relative.parts
            or restored_relative.parts[0] != group
        ):
            corrupt.append(f"unsafe restored path: {restored_path}")
            continue
        target = workspace / DESTINATIONS[group] / Path(*restored_relative.parts[1:])
        if target.exists() and not args.overwrite:
            conflicts.append(str(target))
        else:
            pending.append((source, target))

    if corrupt:
        print(json.dumps({"status": "BLOCKED", "corrupt_or_unknown": corrupt}, indent=2))
        return 2

    result = {
        "status": "DRY-RUN" if not args.apply else "PASS",
        "verified_files": len(manifest["files"]),
        "writes": len(pending),
        "conflicts": len(conflicts),
        "overwrite_enabled": args.overwrite,
        "missing_substitutions": missing_substitutions,
        "selftest": args.selftest,
    }
    if conflicts:
        result["next_step"] = "Re-run with --overwrite only after reviewing the existing installation."
    if not args.apply:
        print(json.dumps(result, indent=2))
        return 0

    if missing_substitutions:
        result["status"] = "BLOCKED"
        result["next_step"] = "Copy PLACEHOLDERS.template.json outside Git, fill every value, and pass it with --substitutions."
        print(json.dumps(result, indent=2))
        return 2

    for source, target in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        for placeholder, value in substitutions.items():
            rendered = str(value)
            if target.suffix.lower() in {".json", ".jsonl"}:
                # Placeholders occur inside JSON strings. Escape the content,
                # not the surrounding quotes, so Windows paths cannot create
                # invalid sequences such as \A or \U during restoration.
                rendered = json.dumps(rendered, ensure_ascii=False)[1:-1]
            text = text.replace(placeholder, rendered)
        target.write_text(text, encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
