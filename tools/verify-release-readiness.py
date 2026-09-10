#!/usr/bin/env python3
"""Provider-free v2 documentation, publication, and activation boundary gate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from installer.kit import VERSION, scan_public_payload


REQUIRED_DOCS = {
    "docs/QUICK-START.md": ("ten-minute", "installed-not-activated", "provider-free"),
    "docs/v2/MIGRATION-GUIDE.md": ("additive migration", "rollback", "recipient"),
    "docs/v2/ADAPTER-AUTHORING.md": ("thin harness adapter", "provider-free", "shared core"),
    "docs/v2/COMPATIBILITY.md": ("Hermes OS", "OpenCode", "MOA"),
    f"docs/v2/RELEASE-NOTES-{VERSION}.md": (VERSION, "eight", "v1"),
    "docs/v2/RELEASE-CHECKLIST.md": ("Publication gate", "Activation gate", "SHA-256"),
    "docs/v2/ACTIVATION.md": ("installed-not-activated", "shadow", "without a Phase 83"),
}
EXPECTED_ADAPTERS = {
    "hermes-os-reference",
    "opencode",
    "pi-coding-agent",
    "antigravity",
    "codex",
    "claude-code",
    "moa",
    "cursor",
}
LINK_DOCS = set(REQUIRED_DOCS) | {"README.md", "docs/v2/README.md"}
FORBIDDEN_HISTORY_ROOTS = {"system-snapshot", ".private-denylist"}
HISTORY_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(
        r"(?i)(?:api[_-]?key|password|secret|bearer[_-]?token)\s*[=:]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}"
    ),
    "home_path": re.compile(
        r"(?i)(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s]+|/(?:home|Users)/[^/\s]+)"
    ),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed")
    return result.stdout


def history_summary() -> dict:
    """Scan reachable patch history without returning matched content or identity."""
    objects = _git("rev-list", "--objects", "--all").splitlines()
    forbidden_paths = set()
    for line in objects:
        parts = line.split(" ", 1)
        if len(parts) == 2 and any(part in FORBIDDEN_HISTORY_ROOTS for part in Path(parts[1]).parts):
            forbidden_paths.add(parts[1])

    patch = _git("log", "--all", "--format=", "--patch", "--no-color", "--no-ext-diff", "--text")
    findings = Counter()
    for kind, pattern in HISTORY_PATTERNS.items():
        for match in pattern.finditer(patch):
            token = match.group(0).lower()
            if kind == "home_path" and any(marker in token for marker in ("alice", "__linux_user__", "__windows_user__", "<")):
                findings["approved_placeholder"] += 1
            elif kind == "email" and token.endswith("@example.com"):
                findings["approved_placeholder"] += 1
            elif kind == "credential_assignment" and any(marker in token for marker in ("abcdefghijklmnop1234", "synthetic", "canary")):
                findings["synthetic_fixture"] += 1
            else:
                findings[kind] += 1

    emails = [line.strip() for line in _git("log", "--all", "--format=%ae").splitlines() if line.strip()]
    non_noreply_authors = sum(not value.lower().endswith("@users.noreply.github.com") for value in emails)
    return {
        "reachable_objects": len(objects),
        "forbidden_historical_paths": len(forbidden_paths),
        "content_findings": dict(sorted(findings.items())),
        "commits": len(emails),
        "non_noreply_author_commits": non_noreply_authors,
        "public_source_gate": "HOLD" if forbidden_paths or non_noreply_authors or any(
            findings.get(kind, 0) for kind in HISTORY_PATTERNS
        ) else "PASS",
    }


def verify_documents() -> list[str]:
    failures = []
    for relative, phrases in REQUIRED_DOCS.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing document:{relative}")
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                failures.append(f"missing phrase:{relative}:{phrase}")
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for relative in sorted(LINK_DOCS):
        path = ROOT / relative
        if not path.is_file():
            continue
        for target in link_pattern.findall(path.read_text(encoding="utf-8")):
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).resolve().exists():
                failures.append(f"broken local link:{relative}:{target}")
    return failures


def verify_claims() -> list[str]:
    failures = []
    matrix = json.loads((ROOT / "modules/adapters/compatibility-matrix.json").read_text(encoding="utf-8"))
    adapters = {item.get("id") for item in matrix.get("adapters", [])}
    if adapters != EXPECTED_ADAPTERS:
        failures.append("adapter inventory")
    if any(item.get("qualification") != "source-qualified" or item.get("runtime_activation") != "unchanged" for item in matrix.get("adapters", [])):
        failures.append("adapter qualification or activation claim")
    policy = json.loads((ROOT / "modules/orchestration-core/core-policy.json").read_text(encoding="utf-8"))
    if policy.get("production_activation_allowed") is not False:
        failures.append("production base policy")
    if set(policy.get("required_promotion_gates", [])) != {"quality", "calls", "latency", "rollback", "tokens"}:
        failures.append("promotion gate inventory")
    if matrix.get("sdk_version") != VERSION:
        failures.append("stable version identity")
    tags = set(_git("tag", "--list").splitlines())
    stable_tag = f"v{VERSION}"
    if stable_tag in tags and _git("rev-parse", f"{stable_tag}^{{commit}}").strip() != _git("rev-parse", "HEAD").strip():
        failures.append("stable tag does not identify HEAD")
    return failures


def _qualifier():
    spec = importlib.util.spec_from_file_location("release_qualifier", ROOT / "tools/qualify-clean-room.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_pair(receipts: list[dict], source_hash: str, commit: str, qualifier_hash: str, require_committed: bool = False) -> list[str]:
    failures = []
    if len(receipts) != 2:
        return ["fresh Windows and Linux qualification receipts required"]
    if {x.get("runtime", {}).get("system") for x in receipts} != {"Windows", "Linux"}:
        failures.append("Windows and Linux runtime coverage required")
    modes = {x.get("source_mode") for x in receipts}
    if len(modes) != 1 or not modes <= {"working-tree", "committed"}:
        failures.append("qualification source modes disagree")
    if require_committed and modes != {"committed"}:
        failures.append("publication requires committed-source qualification")
    required = {"v1-ancestry", "source-verifiers", "reproducible-payload", "reproducible-bundle", "install-doctor-verify", "all-harness-connect", "v1-upgrade-rollback", "managed-uninstall"}
    harnesses = {"hermes", "opencode", "pi", "antigravity", "codex", "claude-code", "moa", "cursor"}
    for receipt in receipts:
        if receipt.get("schema") != "lapart.clean-room-platform.v2" or receipt.get("decision") != "CLEAN_ROOM_PASS":
            failures.append("qualification schema or decision")
        if receipt.get("source_tree_sha256") != source_hash or receipt.get("source_commit") != commit:
            failures.append("qualification does not match current source")
        if receipt.get("v1_commit") != _qualifier().V1_COMMIT:
            failures.append("qualification rollback baseline")
        checks = receipt.get("checks", [])
        by_name = {x.get("name"): x for x in checks}
        source_check = "clean-local-clone" if receipt.get("source_mode") == "committed" else "source-snapshot"
        if len(checks) != len(by_name) or set(by_name) != required | {source_check} or any(x.get("status") != "PASS" for x in checks):
            failures.append("qualification check inventory or status")
        coverage = by_name.get("all-harness-connect", {})
        if set(coverage.get("harnesses", [])) != harnesses or coverage.get("count") != 8:
            failures.append("qualification omits a harness")
        if by_name.get("source-verifiers", {}).get("count") != len(_qualifier().SOURCE_VERIFIERS):
            failures.append("source verifier coverage")
        artifacts = receipt.get("artifacts", {})
        if artifacts.get("qualifier_sha256") != qualifier_hash:
            failures.append("qualifier source changed")
        for key in ("payload_tree_sha256", "bundle_sha256", "release_manifest_sha256", "qualifier_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(artifacts.get(key, ""))):
                failures.append("invalid artifact hash:" + key)
        count = artifacts.get("payload_files")
        if type(count) is not int or count <= 0 or by_name.get("reproducible-payload", {}).get("files") != count:
            failures.append("empty or inconsistent payload")
        if any(receipt.get(key) != 0 for key in ("provider_calls", "provider_tokens", "service_restarts")) or receipt.get("pay_per_token") is not False or receipt.get("live_activation") != "unchanged":
            failures.append("qualification safety accounting")
    if receipts[0].get("artifacts") != receipts[1].get("artifacts"):
        failures.append("Windows and Linux release artifacts differ")
    return failures


def verify(receipt_paths: list[Path] | None = None, require_committed: bool = False, visibility: str = "private") -> tuple[list[str], dict]:
    failures = verify_documents() + verify_claims()
    privacy_hits = scan_public_payload(ROOT)
    if privacy_hits:
        failures.append(f"public payload privacy:{len(privacy_hits)}")
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (receipt_paths or [])]
    qualifier = _qualifier()
    failures.extend(validate_pair(receipts, qualifier.source_digest(ROOT), _git("rev-parse", "HEAD").strip(), hashlib.sha256((ROOT / "tools/qualify-clean-room.py").read_bytes()).hexdigest(), require_committed))
    if require_committed and _git("status", "--porcelain").strip():
        failures.append("publication requires a clean checkout")
    history = history_summary()
    if visibility == "public" and history["public_source_gate"] != "PASS":
        failures.append("public source history remains on hold")
    summary = {
        "schema": "lapart.release-readiness-summary.v2",
        "decision": "HOLD" if failures else ("READY_FOR_PRIVATE_RELEASE_APPROVAL" if require_committed and visibility == "private" else "READY_FOR_RELEASE_APPROVAL" if require_committed else "READY_FOR_COMMIT_APPROVAL"),
        "version": VERSION,
        "public_payload_privacy_findings": len(privacy_hits),
        "historical_source": history,
        "intended_repository_visibility": visibility,
        "published": False,
        "runtime_activation": "unchanged",
        "provider_calls": 0,
        "provider_tokens": 0,
        "service_restarts": 0,
    }
    return failures, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", action="append", type=Path, default=[])
    parser.add_argument("--require-committed", action="store_true")
    parser.add_argument("--visibility", choices=("private", "public"), default="private")
    args = parser.parse_args()
    try:
        failures, summary = verify(args.receipt, args.require_committed, args.visibility)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("HOLD: release evidence could not be verified (" + type(exc).__name__ + ")")
        return 1
    print(json.dumps(summary, indent=2))
    for failure in failures:
        print("HOLD: " + failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
