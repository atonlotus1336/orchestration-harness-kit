#!/usr/bin/env python
"""
Read-only test harness for dry-run-gate.py (R4: every gate names its failing
inputs and demonstrates them). Feeds synthetic PreToolUse envelopes to the
hook via stdin; executes NOTHING.

Cases:
  1. Every rule's failing_input from destructive-patterns.json -> expected
     verdict (ask/deny).
  2. Every protected_write failing_input -> Write is asked.
  3. Negative controls (benign commands + benign writes) -> silence.
  4. Mixed-target regression: destructive verb naming BOTH a disposable path
     and a protected path -> must still fire (disposable must not mask).
  5. Fallback: GUARD_POLICY pointed at a missing file -> embedded
     rules still fire on rm -rf.
  6. Precision regressions (policy 1.1.0 / hook v3): read-only and
     document-only commands stay silent; executable variants keep their
     verdict; a safe prefix no longer exempts a chained command.
  7. Fallback precision: the embedded docker rule (not in the template
     policy) ignores read-only mentions of a service name.
  8. MultiEdit (file_path) and NotebookEdit (notebook_path) reach the same
     protected_write check as Write/Edit.
Exit 0 = all pass; nonzero = failures listed.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "dry-run-gate.py")
POLICY = os.path.join(HERE, "destructive-patterns.json")

NEGATIVE_CONTROLS = [
    ("Bash", {"command": "git status"}),
    ("Bash", {"command": "ls -la /data/example-vault"}),
    ("Bash", {"command": "mkdir -p /data/other-project/.planning"}),
    ("PowerShell", {"command": r"Get-ChildItem C:\Users\alice\other"}),
    ("Bash", {"command": "rm -rf node_modules"}),  # disposable-only target
    ("Write", {"file_path": "/data/other-project/.planning/STATE.md"}),
    ("Edit", {"file_path": "/data/other-project/notes/index.md"}),
]

MIXED_TARGET = ("Bash", {"command": "rm -rf scratchpad /data/example-vault/Memory"})

# 6. precision regressions (policy 1.1.0 + hook v3, 2026-10-15).
# Each row: (command, expected verdict or None for silent). The first block
# was over-blocked before 1.1.0; the second block must keep firing. DENY is
# the template's example deny rule; swap in your own incident class.
DENY = "mytool daemon run --replace"
PRECISION = [
    # --- must now be silent (or downgraded) ---
    ("grep -rn mytool-daemon docs/", None),
    ("python x.py > /tmp/out.json", None),
    ("python x.py > /home/alice/scratchpad/out.json", None),
    ("echo x >> notes.md", None),
    ("python -c \"print('a -> b.md')\"", None),
    ("cmd 2>&1 | tee log.txt", None),
    ('grep -n "%s" docs/a.md 2>/dev/null' % DENY, None),
    ("cat > /tmp/notes.md <<'EOF'\nquote: %s\nEOF" % DENY, None),
    ("cat > notes.md <<'EOF'\nquote: %s\nEOF" % DENY, "ask"),  # doc overwrite: ask, not deny
    # --- must keep firing ---
    ("echo x > /data/example-vault/example-app/config.yaml", "ask"),
    ("python x.py > out.json", "ask"),
    ('grep x f > /data/example-vault/out.md', "ask"),
    ("grep -l x f | xargs rm -rf /data/example-vault/Memory", "ask"),
    ("grep -n x f; %s" % DENY, "deny"),
    ("bash -lc 'grep -n x f; %s'" % DENY, "deny"),
    ('grep "$(%s)" f' % DENY, "deny"),
    ('rg --pre "%s" x' % DENY, "deny"),
    ("cat <<'EOF' | bash\n%s\nEOF" % DENY, "deny"),
    ("cat > run.sh <<'EOF'\n%s\nEOF" % DENY, "deny"),
    ("python3 - <<'PY'\nimport os; os.system('%s')\nPY" % DENY, "deny"),
    ("cat > /tmp/n.md <<EOF\nok\nEOF\n%s" % DENY, "deny"),  # real command after the body
    # --- safe-prefix chaining hole (found 2026-10-15) ---
    ("ls && rm -rf /data/example-vault/Memory", "ask"),
    ("cat f; %s" % DENY, "deny"),
    ("echo ok | %s" % DENY, "deny"),
    ("ls -la /data/example-vault", None),  # a single safe command stays silent
]

# 7. the embedded fallback's docker rule (policy 1.1.0 alternation fix): the
# bare service name must no longer match read-only status/log/grep commands.
NO_POLICY = {"GUARD_POLICY": "/nonexistent/policy.json"}
FALLBACK_PRECISION = [
    ("systemctl --user status hermes-gateway.service", None),
    ("journalctl --user -u hermes-gateway -n 5", None),
    ("docker logs hermes-gateway", None),
    ("docker rm -f hermes-gateway", "ask"),
    ("docker restart --time 30 hermes-gateway", "ask"),
    ("docker compose -f c.yml stop hermes-gateway", "ask"),
]


def run_gate(tool, tool_input, env_override=None):
    env = dict(os.environ)
    if env_override:
        env.update(env_override)
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    proc = subprocess.run(
        [sys.executable, GATE], input=payload, capture_output=True,
        text=True, env=env, timeout=30,
    )
    if not proc.stdout.strip():
        return None  # silent = benign
    out = json.loads(proc.stdout)
    return out["hookSpecificOutput"]["permissionDecision"]


def main():
    with open(POLICY, encoding="utf-8") as f:
        policy = json.load(f)

    failures, passes = [], 0

    # 1. every rule fires with its declared verdict
    for rule in policy["rules"]:
        fi = rule.get("failing_input")
        if not fi:
            failures.append("VOID GATE: rule %s has no failing_input" % rule["id"])
            continue
        expected = rule.get("verdict", "ask")
        got = run_gate("Bash", {"command": fi})
        if got == expected:
            passes += 1
            print("PASS  rule %-32s -> %s" % (rule["id"], got))
        else:
            failures.append("rule %s: expected %s, got %s (input: %s)"
                            % (rule["id"], expected, got, fi))

    # 2. protected_write targets are asked
    for entry in policy.get("protected_write", []):
        fi = entry.get("failing_input")
        if not fi:
            failures.append("VOID GATE: protected_write %r has no failing_input" % entry["label"])
            continue
        got = run_gate("Write", {"file_path": fi})
        if got == "ask":
            passes += 1
            print("PASS  write %-32s -> ask" % entry["label"][:32])
        else:
            failures.append("protected_write %s: expected ask, got %s" % (entry["label"], got))

    # 3. negative controls stay silent
    for tool, ti in NEGATIVE_CONTROLS:
        got = run_gate(tool, ti)
        if got is None:
            passes += 1
            print("PASS  benign %-31s -> silent" % (list(ti.values())[0][:31]))
        else:
            failures.append("negative control fired (%s): %s %r" % (got, tool, ti))

    # 4. mixed-target regression: disposable must not mask a protected path
    got = run_gate(*MIXED_TARGET)
    if got == "ask":
        passes += 1
        print("PASS  mixed-target (disposable+protected)  -> ask")
    else:
        failures.append("mixed-target regression: expected ask, got %s" % got)

    # 6. precision regressions
    for cmd, expected in PRECISION:
        got = run_gate("Bash", {"command": cmd})
        label = cmd.replace("\n", " \\n ")[:44]
        if got == expected:
            passes += 1
            print("PASS  precision %-44s -> %s" % (label, got or "silent"))
        else:
            failures.append("precision: expected %s, got %s (input: %r)" % (expected, got, cmd))

    # 7. fallback precision (embedded docker rule)
    for cmd, expected in FALLBACK_PRECISION:
        got = run_gate("Bash", {"command": cmd}, env_override=NO_POLICY)
        if got == expected:
            passes += 1
            print("PASS  fallback-precision %-35s -> %s" % (cmd[:35], got or "silent"))
        else:
            failures.append("fallback precision: expected %s, got %s (input: %r)"
                            % (expected, got, cmd))

    # 8. MultiEdit (file_path) and NotebookEdit (notebook_path) reach the same
    # protected_write check as Write/Edit.
    for tool, ti, expected in [
        ("MultiEdit", {"file_path": "/data/example-vault/.env"}, "ask"),
        ("NotebookEdit", {"notebook_path": "/home/alice/.claude/hooks/x.ipynb"}, "ask"),
        ("MultiEdit", {"file_path": "/data/other-project/notes/index.md"}, None),
        ("NotebookEdit", {"notebook_path": "/data/other-project/.planning/scratch.ipynb"}, None),
    ]:
        got = run_gate(tool, ti)
        if got == expected:
            passes += 1
            print("PASS  %-12s %-34s -> %s" % (tool, list(ti.values())[0][:34], got or "silent"))
        else:
            failures.append("%s: expected %s, got %s (input: %r)" % (tool, expected, got, ti))

    # 5. embedded fallback still guards when the JSON is gone
    got = run_gate("Bash", {"command": "rm -rf /data/example-vault/Memory"},
                   env_override=NO_POLICY)
    if got == "ask":
        passes += 1
        print("PASS  fallback (policy missing)            -> ask")
    else:
        failures.append("fallback: expected ask with missing policy, got %s" % got)

    print()
    print("%d passed, %d failed" % (passes, len(failures)))
    for f in failures:
        print("FAIL  " + f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
