#!/usr/bin/env python3
"""Exercise the Git-clone Prime/OMP restore and Hermes registration path."""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if not args.selftest:
        parser.error("this verifier currently supports --selftest only")

    try:
        import yaml
    except ImportError:
        print(json.dumps({"status": "BLOCKED", "reason": "pyyaml_missing"}))
        return 2

    template_path = ROOT / "system-snapshot" / "PLACEHOLDERS.template.json"
    if not template_path.is_file():
        print(json.dumps({
            "status": "SKIPPED",
            "reason": "contract_only_payload_has_no_private_snapshot",
            "next_action": "Use an authorized full Git clone for Prime/OMP bridge restoration.",
        }))
        return 0

    failures: list[str] = []
    details: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="prime-omp-client-path-") as raw:
        temp = Path(raw)
        workspace = temp / "client-workspace"
        substitutions = {
            key: f"client-value-{index:02d}"
            for index, key in enumerate(
                json.loads(template_path.read_text(encoding="utf-8")), start=1
            )
        }
        substitutions.update({
            "__LINUX_USER__": "clientuser",
            "__WINDOWS_USER__": "ClientUser",
            "__WSL_DISTRO__": "Ubuntu-24.04",
        })
        substitutions_path = temp / "substitutions.json"
        substitutions_path.write_text(json.dumps(substitutions), encoding="utf-8")

        incomplete = dict(substitutions)
        incomplete.pop("__WSL_DISTRO__", None)
        incomplete_path = temp / "incomplete.json"
        incomplete_path.write_text(json.dumps(incomplete), encoding="utf-8")
        rejected = run([
            sys.executable, str(ROOT / "tools" / "restore-private-system-snapshot.py"),
            "--workspace-root", str(temp / "rejected"),
            "--substitutions", str(incomplete_path), "--apply",
        ])
        if rejected.returncode == 0 or "__WSL_DISTRO__" not in rejected.stdout:
            failures.append("missing_distro_was_not_rejected")

        restored = run([
            sys.executable, str(ROOT / "tools" / "restore-private-system-snapshot.py"),
            "--workspace-root", str(workspace),
            "--substitutions", str(substitutions_path), "--apply",
        ])
        if restored.returncode != 0:
            failures.append("snapshot_restore_failed")
        else:
            bridge = workspace / "hermes-agent-self-evolution"
            profiles = temp / "profiles"
            profile = profiles / "workspace"
            profile.mkdir(parents=True)
            original = {
                "providers": {"local": {"type": "example"}},
                "memory": {"enabled": True},
                "toolsets": ["default"],
                "mcp_servers": {"existing": {"command": "existing-server"}},
            }
            (profile / "config.yaml").write_text(
                yaml.safe_dump(original, sort_keys=False), encoding="utf-8"
            )
            rendered = run([
                sys.executable, str(bridge / "scripts" / "render-prime-profile-overlay.py"),
                "--profiles-root", str(profiles), "--profile", "workspace",
                "--policy", str(bridge / "config" / "prime-profile-policy.json"),
                "--ledger", str(temp / "jobs.sqlite3"),
            ])
            candidate = profile / "config.prime-next.yaml"
            if rendered.returncode != 0 or not candidate.is_file():
                failures.append("hermes_overlay_render_failed")
            else:
                value = yaml.safe_load(candidate.read_text(encoding="utf-8"))
                for key in ("providers", "memory", "toolsets"):
                    if value.get(key) != original[key]:
                        failures.append(f"profile_section_changed:{key}")
                servers = value.get("mcp_servers", {})
                if servers.get("existing") != original["mcp_servers"]["existing"]:
                    failures.append("existing_mcp_changed")
                prime = servers.get("prime-agent", {})
                if prime.get("env", {}).get("WSL_DISTRO_NAME") != "Ubuntu-24.04":
                    failures.append("recipient_distro_not_rendered")
                launch_text = json.dumps(prime, sort_keys=True)
                if "__WSL_DISTRO__" in launch_text or "__WSL_WORKSPACE__" in launch_text:
                    failures.append("unresolved_prime_placeholders")

            try:
                tree = ast.parse(
                    (bridge / "prime_bridge" / "mcp_server.py").read_text(encoding="utf-8-sig")
                )
                names = {
                    keyword.value.value
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    for keyword in node.keywords
                    if keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                    and keyword.value.value.startswith("prime_")
                }
            except (OSError, SyntaxError) as exc:
                failures.append("prime_bridge_inventory_unreadable")
                details["prime_bridge_inventory_unreadable"] = str(exc)
                names = set()
            for expected in ("prime_delegate", "prime_auto_session_start", "prime_bridge_health"):
                if expected not in names:
                    failures.append(f"missing_tool:{expected}")

    result = {
        "status": "PASS" if not failures else "FAIL",
        "gate": "git-clone-prime-omp-client-path",
        "checks": 8,
        "failures": failures,
        "details": details,
        "scope": {
            "proves": ["snapshot_restore", "recipient_substitution", "hermes_profile_overlay", "prime_mcp_source_inventory"],
            "requires_live_followup": ["bridge_dependency_import", "hermes_mcp_handshake", "prime_runtime", "omp_runtime"],
        },
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
