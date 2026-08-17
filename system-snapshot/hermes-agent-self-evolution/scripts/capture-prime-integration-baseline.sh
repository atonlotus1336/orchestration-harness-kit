#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'prime baseline: FAIL: %s\n' "$*" >&2
  exit 1
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ "$repo_root" == "__WSL_WORKSPACE__/hermes-agent-self-evolution" ]] || die "wrong repository"
[[ "${WSL_DISTRO_NAME:-}" == "Ubuntu-restored" ]] || die "must run in Ubuntu-restored"
[[ "$(id -u)" != "0" ]] || die "must run as the normal WSL user"
bash "$repo_root/scripts/sync-phase01-tools-env.sh" --check >/dev/null
python_bin="$(bash "$repo_root/scripts/sync-phase01-tools-env.sh" --python)"

case "${1:-}" in
  capture)
    [[ $# -eq 1 ]] || die "capture takes no arguments"
    mode="capture"
    before=""
    after=""
    ;;
  compare)
    [[ $# -eq 3 ]] || die "compare requires BEFORE AFTER"
    mode="compare"
    before="$2"
    after="$3"
    ;;
  *) die "usage: $0 capture | compare BEFORE AFTER" ;;
esac

exec "$python_bin" - "$repo_root" "$mode" "$before" "$after" <<'PY'
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


REPO, MODE = Path(sys.argv[1]), sys.argv[2]
SCHEMA = json.loads((REPO / "contracts/hermes-baseline-evidence.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
UTC = dt.timezone.utc


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "sha256": sha(resolved.read_bytes()),
        "uid": stat.st_uid,
        "gid": stat.st_gid,
        "mode": stat.st_mode & 0o7777,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def yaml_block(data: bytes, name: str) -> bytes:
    text = data.decode("utf-8", errors="strict").replace("\r\n", "\n")
    lines = text.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"^{re.escape(name)}\s*:\s*(?:#.*)?$", line.rstrip("\n")):
            start = index
            break
    if start is None:
        return b""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = index
            break
    return "".join(lines[start:end]).encode("utf-8")


def proc_bytes(pid: int, name: str) -> bytes:
    return (Path("/proc") / str(pid) / name).read_bytes()


def listener(port: int) -> dict[str, Any]:
    completed = subprocess.run(
        ["/usr/bin/ss", "-ltnp"], capture_output=True, text=True, timeout=5, check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    if completed.returncode != 0:
        return {"status": "UNVERIFIED", "port": port, "address": "", "pid": None}
    lines = [line for line in completed.stdout.splitlines() if re.search(rf":{port}\s", line)]
    if len(lines) != 1:
        return {"status": "NOT_LISTENING" if not lines else "UNVERIFIED", "port": port, "address": "", "pid": None}
    line = lines[0]
    pid_match = re.search(r"pid=(\d+)", line)
    address_match = re.search(r"LISTEN\s+\d+\s+\d+\s+(\S+):" + str(port) + r"\s", line)
    if pid_match is None or address_match is None:
        return {"status": "UNVERIFIED", "port": port, "address": "", "pid": None}
    return {"status": "OBSERVED", "port": port, "address": address_match.group(1), "pid": int(pid_match.group(1))}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def health() -> dict[str, Any]:
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request("http://127.0.0.1:8642/health", method="GET")
    try:
        with opener.open(request, timeout=5) as response:
            body = response.read(65537)
            code = response.status
            final_url = response.geturl()
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return {"status": "UNAVAILABLE", "http_status": None, "url": "http://127.0.0.1:8642/health", "body_base64": "", "body_sha256": ""}
    status = "UNVERIFIED"
    if code == 200 and final_url == request.full_url and len(body) <= 65536:
        try:
            parsed = json.loads(body)
            if (
                isinstance(parsed, dict)
                and set(parsed) == {"status", "platform", "version"}
                and parsed["status"] == "ok"
                and parsed["platform"] == "hermes-agent"
                and isinstance(parsed["version"], str)
                and parsed["version"]
            ):
                status = "READY"
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return {
        "status": status,
        "http_status": code,
        "url": final_url,
        "body_base64": base64.b64encode(body).decode("ascii"),
        "body_sha256": sha(body),
    }


def capture() -> dict[str, Any]:
    gateway = listener(8642)
    dashboard = listener(9119)
    health_value = health()
    identity: dict[str, Any] = {"status": "UNVERIFIED"}
    authority: dict[str, Any] = {"status": "UNVERIFIED"}
    readiness = {
        "gateway_liveness": health_value["status"],
        "mcp": "UNVERIFIED",
        "rag": "UNVERIFIED",
        "memory": "UNVERIFIED",
        "toolsets": "UNVERIFIED",
    }

    pid = gateway.get("pid")
    if isinstance(pid, int):
        proc = Path("/proc") / str(pid)
        try:
            cmdline_raw = proc_bytes(pid, "cmdline")
            arguments = [item.decode("utf-8") for item in cmdline_raw.split(b"\0") if item]
            profile_indexes = [index for index, item in enumerate(arguments) if item == "--profile"]
            profile = arguments[profile_indexes[0] + 1] if len(profile_indexes) == 1 and profile_indexes[0] + 1 < len(arguments) else ""
            executable = Path(os.readlink(proc / "exe")).resolve(strict=True)
            script_candidates = [Path(item) for item in arguments[1:] if item.startswith("/") and Path(item).is_file()]
            hermes_script = next((item for item in script_candidates if item.name == "hermes"), None)
            stat_fields = (proc / "stat").read_text(encoding="utf-8").split()
            uid = int((proc / "status").read_text(encoding="utf-8").split("Uid:", 1)[1].split()[0])
            runtime_config = Path.home() / ".hermes" / "profiles" / profile / "config.yaml"
            source_candidates = sorted(Path("/mnt/c/Users").glob(f"*/.hermes/profiles/{profile}/config.yaml"))
            if len(source_candidates) != 1:
                raise ValueError(f"expected one managed Windows profile source, found {len(source_candidates)}")
            source_config = source_candidates[0]
            runtime_data = runtime_config.read_bytes()
            source_data = source_config.read_bytes()
            sections = {
                name: {"present": bool(block := yaml_block(runtime_data, name)), "sha256": sha(block) if block else ""}
                for name in ("providers", "mcp_servers", "memory", "toolsets")
            }
            all_cmdlines = []
            for candidate in Path("/proc").iterdir():
                if not candidate.name.isdigit():
                    continue
                try:
                    all_cmdlines.append((candidate / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="ignore"))
                except (OSError, PermissionError):
                    continue
            joined = "\n".join(all_cmdlines)
            readiness.update({
                "mcp": "READY" if sections["mcp_servers"]["present"] and "mcp_stdio_watchdog.py" in joined else "UNVERIFIED",
                "rag": "READY" if "rag-orchestrator" in joined else "UNVERIFIED",
                "memory": "READY" if sections["memory"]["present"] and "memoryweaver" in joined else "UNVERIFIED",
                "toolsets": "READY" if sections["toolsets"]["present"] else "UNVERIFIED",
            })
            identity = {
                "status": "READY" if profile and uid == os.getuid() and hermes_script is not None else "UNVERIFIED",
                "pid": pid,
                "start_ticks": int(stat_fields[21]),
                "uid": uid,
                "profile": profile,
                "cmdline_sha256": sha(cmdline_raw),
                "executable": file_identity(executable),
                "hermes_entrypoint": file_identity(hermes_script) if hermes_script is not None else None,
            }
            authority = {
                "status": "READY" if profile and source_config.is_file() else "UNVERIFIED",
                "profile": profile,
                "runtime_config": file_identity(runtime_config),
                "managed_source": file_identity(source_config),
                "sections": sections,
            }
        except (OSError, ValueError, IndexError, UnicodeDecodeError):
            pass

    bindings = {
        "gateway_8642": {
            **gateway,
            "expected": "0.0.0.0",
            "match": gateway.get("address") == "0.0.0.0",
        },
        "dashboard_9119": {
            **dashboard,
            "expected": "127.0.0.1-or-not-listening",
            "match": dashboard.get("status") == "NOT_LISTENING" or dashboard.get("address") in {"127.0.0.1", "[::1]", "::1"},
        },
    }
    ready = (
        health_value["status"] == "READY"
        and identity.get("status") == "READY"
        and authority.get("status") == "READY"
        and all(value == "READY" for value in readiness.values())
        and all(value["match"] for value in bindings.values())
    )
    stable = {
        "schema_version": "2.0",
        "source_kind": "LIVE",
        "status": "READY" if ready else "UNVERIFIED",
        "go_eligible": ready,
        "health": health_value,
        "listener_identity": identity,
        "authority": authority,
        "readiness": readiness,
        "bindings": bindings,
        "remote_reachability": {"status": "UNVERIFIED", "attestation_sha256": ""},
    }
    return {
        "captured_at": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_nonce": uuid.uuid4().hex,
        **stable,
        "canonical_fingerprint_sha256": sha(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")),
    }


def validate(value: dict[str, Any]) -> None:
    errors = sorted(VALIDATOR.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "root"
        raise SystemExit(f"prime baseline: FAIL: schema at {location}: {errors[0].message}")
    timestamp = dt.datetime.fromisoformat(value["captured_at"].replace("Z", "+00:00"))
    now = dt.datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp > now + dt.timedelta(minutes=1) or now - timestamp > dt.timedelta(minutes=15):
        raise SystemExit("prime baseline: FAIL: capture timestamp is stale or future")
    stable = {
        key: item
        for key, item in value.items()
        if key not in {"captured_at", "run_nonce", "canonical_fingerprint_sha256"}
    }
    expected = sha(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if value["canonical_fingerprint_sha256"] != expected:
        raise SystemExit("prime baseline: FAIL: canonical fingerprint mismatch")


if MODE == "capture":
    value = capture()
    validate(value)
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
else:
    before = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    after = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
    validate(before)
    validate(after)
    excluded = {"captured_at", "run_nonce"}
    before_stable = {key: value for key, value in before.items() if key not in excluded}
    after_stable = {key: value for key, value in after.items() if key not in excluded}
    equal = before_stable == after_stable
    result = {
        "status": "PASS" if equal else "FAIL",
        "equal": equal,
        "excluded_fields": sorted(excluded),
        "before_fingerprint": before["canonical_fingerprint_sha256"],
        "after_fingerprint": after["canonical_fingerprint_sha256"],
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    raise SystemExit(0 if equal else 3)
PY
