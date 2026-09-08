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

    # 5. embedded fallback still guards when the JSON is gone
    got = run_gate("Bash", {"command": "rm -rf /data/example-vault/Memory"},
                   env_override={"GUARD_POLICY": "/nonexistent/policy.json"})
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
