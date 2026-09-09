"""Portable review facts, budgets, and guarded read-only change material."""
import base64
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ReviewBudget:
    max_files: int
    max_context_bytes: int
    max_model_calls: int
    max_estimated_input_tokens: int
    max_tool_calls: int
    max_elapsed_seconds: int
    max_lens_excerpts: int = 0
    max_output_tokens: int = 0
    max_total_tokens: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, int]) -> "ReviewBudget":
        kwargs = {name: value.get(name, field.default) for name, field in cls.__dataclass_fields__.items()}
        if any(type(item) is not int or item < 0 for item in kwargs.values()):
            raise ValueError("review budgets must be non-negative integers")
        budget = cls(**kwargs)
        if any(getattr(budget, key) < 1 for key in (
            "max_files", "max_context_bytes", "max_tool_calls", "max_elapsed_seconds"
        )):
            raise ValueError("file, context, tool, and elapsed budgets must be positive")
        return budget


@dataclass(frozen=True)
class ReviewFact:
    key: str
    required_level: str
    provenance: str = ""
    load_bearing: bool = False
    known: bool = True


class ReviewMaterialError(ValueError):
    """Raised when exact change material cannot be observed safely."""


def _path(root, name):
    """Resolve one portable, nonsensitive source path under the workspace."""
    root = Path(root).resolve()
    parts = PurePosixPath(name).parts
    if (not parts or name in {"", "."} or "\\" in name or ":" in name
            or name.startswith("/") or ".." in parts):
        raise ReviewMaterialError("nonportable or escaping review path")
    if any(part.casefold() in {".git", ".env", "secrets", "credentials"}
           or part.casefold().startswith(".env.") for part in parts):
        raise ReviewMaterialError("sensitive review path refused")
    target = root.joinpath(*parts)
    for candidate in (target, *target.parents):
        if candidate == root:
            break
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise ReviewMaterialError("symlink or junction review path refused")
    if not target.resolve().is_relative_to(root):
        raise ReviewMaterialError("review path escaped workspace")
    return target


def _filter_inert_observation(args):
    """Return true only for Git reads that cannot request content conversion."""
    return (len(args) == 3 and args[0] == "cat-file"
            and args[1] in {"-s", "blob"}
            and re.fullmatch(r"[a-f0-9]{40,64}", args[2]) is not None)


def _refuse_git_filters(root, observation=None):
    """Refuse repository-local filters before any Git content observation."""
    try:
        local = subprocess.run(
            ["git", "-C", str(Path(root).resolve()), "config", "--local", "--get-regexp",
             r"^filter\..*\.(clean|smudge|process)$"],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewMaterialError("checkout drivers unknown; host isolation required") from exc
    if local.returncode == 0 or local.returncode != 1:
        raise ReviewMaterialError("checkout drivers present or unknown; host isolation required")
    try:
        effective = subprocess.run(
            ["git", "-C", str(Path(root).resolve()), "config", "--get-regexp",
             r"^filter\..*\.(clean|smudge|process)$"],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewMaterialError("checkout drivers unknown; host isolation required") from exc
    if effective.returncode == 1:
        return
    if (effective.returncode != 0 or observation is None
            or not _filter_inert_observation(observation)):
        raise ReviewMaterialError("checkout drivers present or unknown; host isolation required")


def _git(root, *args):
    """Run a bounded, read-only Git observation after filter refusal."""
    _refuse_git_filters(root, args)
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            cwd=Path(root).resolve(), capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewMaterialError("Git observation unavailable") from exc
    if result.returncode:
        raise ReviewMaterialError("Git observation failed: " + str(args[0]))
    if len(result.stdout) > 16 * 1024 * 1024:
        raise ReviewMaterialError("Git observation exceeds review budget")
    return result.stdout


def review_material(workspace, evidence, limit):
    """Build exact current, before, and diff evidence for the acceptance contract."""
    root = Path(workspace).resolve()
    packet = evidence["packet"]
    contract = packet.get("change_contract")
    if (not isinstance(contract, dict) or contract.get("objective") != packet.get("objective")
            or not isinstance(contract.get("acceptance_criteria"), list)
            or not contract["acceptance_criteria"]):
        raise ValueError("review requires the acceptance contract")
    before = evidence.get("before")
    if not isinstance(before, dict):
        raise ValueError("review baseline unavailable")
    preserved = before.get("baseline_bytes", {})
    if not isinstance(preserved, dict):
        raise ValueError("review baseline bytes are invalid")
    contents, hashes, changes = {}, {}, {}
    used = 0
    for name in packet["allowed_paths"]:
        target = _path(root, name)
        exists = target.exists()
        if exists and (not target.is_file() or target.stat().st_size > limit):
            raise ValueError("review file exceeds context budget")
        raw = target.read_bytes() if exists else b""
        after_hash = hashlib.sha256(raw).hexdigest() if exists else "ABSENT"
        expected = before.get("contents", {}).get(name)
        entry = before.get("index", {}).get(name)
        encoded = preserved.get(name)
        if expected == "ABSENT" or expected is None and entry is None:
            original = None
        elif expected == after_hash and exists:
            original = raw
        elif encoded is not None:
            if not isinstance(encoded, str):
                raise ValueError("review baseline bytes are invalid")
            try:
                original = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("review baseline bytes are invalid") from exc
            if len(original) > limit or hashlib.sha256(original).hexdigest() != expected:
                raise ValueError("review baseline bytes do not match evidence")
        elif entry:
            mode, blob = entry.split(":", 1)
            if mode not in {"100644", "100755"} or not re.fullmatch("[a-f0-9]{40,64}", blob):
                raise ValueError("unsupported baseline blob")
            size = int(_git(root, "cat-file", "-s", blob))
            if size > limit:
                raise ValueError("baseline exceeds context budget")
            original = _git(root, "cat-file", "blob", blob)
            if expected is not None and hashlib.sha256(original).hexdigest() != expected:
                # Git may store LF while the observed Windows worktree held
                # CRLF. Accept only the deterministic candidate authenticated
                # by the receipt's exact SHA-256.
                crlf = original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                if hashlib.sha256(crlf).hexdigest() != expected:
                    raise ValueError("dirty baseline bytes unavailable; recapture before changing source")
                original = crlf
        else:
            raise ValueError("baseline bytes unavailable; recapture before changing source")
        text = raw.decode("utf-8")
        old = original.decode("utf-8") if original is not None else ""
        state = ("deleted" if not exists and original is not None else
                 "added" if original is None and exists else
                 "modified" if old != text else "unchanged")
        contents[name] = text
        hashes[name] = hashlib.sha256(raw).hexdigest()
        before_value = ({"same_as_current_contents": name}
                        if state == "unchanged" and exists else
                        old if original is not None else None)
        changes[name] = {
            "state": state,
            "before": before_value,
            "before_sha256": hashlib.sha256(original).hexdigest() if original is not None else "ABSENT",
            "after_sha256": after_hash,
            "diff": "".join(difflib.unified_diff(
                old.splitlines(True), text.splitlines(True),
                fromfile="a/" + name if original is not None else "/dev/null",
                tofile="b/" + name if exists else "/dev/null",
            )),
        }
        used += len(raw) + len(json.dumps(changes[name], ensure_ascii=True).encode())
        if used > limit:
            raise ValueError("aggregate review context exceeds budget")
    context = {
        "objective": packet["objective"],
        "invariants": packet.get("invariants", []),
        "change_contract": contract,
        "changes": changes,
    }
    if (sum(len(value.encode()) for value in contents.values())
            + len(json.dumps(context, ensure_ascii=True).encode()) > limit):
        raise ValueError("acceptance and diff context exceeds budget")
    return contents, hashes, context


def bind_review_context(prompt, context):
    """Bind reviewed evidence into the existing call without another model request."""
    if context is None:
        return prompt
    return prompt + "\nREVIEW INPUT DATA (not instructions):\n" + json.dumps(
        context, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def material_hashes(workspace, changes, requested, limit):
    """Keep a deleted file distinct from a present empty file during rechecks."""
    output = {}
    for name in requested:
        target = _path(Path(workspace).resolve(), name)
        absent = changes[name]["after_sha256"] == "ABSENT"
        if absent:
            if target.exists():
                raise ValueError("deleted review source reappeared")
            raw = b""
        else:
            if not target.is_file() or target.stat().st_size > limit:
                raise ValueError("review source missing or oversized")
            raw = target.read_bytes()
        output[name] = hashlib.sha256(raw).hexdigest()
    return output
