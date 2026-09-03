#!/usr/bin/env python3
"""Provider-free v2 documentation, publication, and activation boundary gate."""

from __future__ import annotations

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
    "docs/v2/RELEASE-NOTES-2.0.0.md": ("not tagged or published", "eight", "v1"),
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
    if VERSION != "2.0.0-dev" or matrix.get("sdk_version") != VERSION:
        failures.append("candidate version identity")
    tags = set(_git("tag", "--list").splitlines())
    if "v2.0.0" in tags:
        failures.append("unexpected published version tag")
    return failures


def verify() -> tuple[list[str], dict]:
    failures = verify_documents() + verify_claims()
    privacy_hits = scan_public_payload(ROOT)
    if privacy_hits:
        failures.append(f"public payload privacy:{len(privacy_hits)}")
    phase80 = subprocess.run(
        [sys.executable, str(ROOT / "tools/verify-clean-room-platforms.py")],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if phase80.returncode:
        failures.append("phase80 evidence")
    history = history_summary()
    if history["public_source_gate"] != "HOLD":
        failures.append("historical source hold unexpectedly absent")
    summary = {
        "schema": "lapart.release-readiness-summary.v1",
        "decision": "LOCAL_CANDIDATE_READY_PUBLICATION_HOLD" if not failures else "HOLD",
        "version": VERSION,
        "public_payload_privacy_findings": len(privacy_hits),
        "historical_source": history,
        "repository_visibility": "PRIVATE_OBSERVED_SEPARATELY",
        "published": False,
        "runtime_activation": "unchanged",
        "provider_calls": 0,
        "provider_tokens": 0,
        "service_restarts": 0,
    }
    return failures, summary


def main() -> int:
    failures, summary = verify()
    print(json.dumps(summary, indent=2))
    if failures:
        print("HOLD: release-readiness verification")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: local v2 candidate; publication and activation remain held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
