#!/usr/bin/env python
"""
PreToolUse hook: dry-run safety gate for destructive tool calls (kit edition).

Pattern: the agent may PROPOSE an action; a deterministic simulator predicts
its effects; a human reviews before anything runs. Policy lives in the
sibling destructive-patterns.json (env override: GUARD_POLICY). Per-rule
verdicts: "ask" (default; a false positive costs one keypress) or "deny"
(reserved for command classes with incident history - cite the incident).

Register in Claude Code settings.json:
  "hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell|Write|Edit",
    "hooks": [{"type": "command", "command": "python /path/to/dry-run-gate.py",
               "timeout": 10}]}]}

The embedded fallback below mirrors the generic rules so a missing/unreadable
policy file degrades safely instead of crashing tool calls. EDIT the
example-vault patterns (here and in the JSON) to your real vault paths.
"""
import json
import os
import re
import sys

POLICY_PATH = os.environ.get(
    "GUARD_POLICY",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "destructive-patterns.json"),
)

# --- Embedded fallback (frozen v1 behavior; JSON supersedes when loadable) ---
FALLBACK_RULES = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*f|\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*f[a-zA-Z]*[rR]",
     "recursive force delete of a directory tree", "HIGH"),
    (r"Remove-Item\b[^\n]*-Recurse", "recursive delete (PowerShell)", "HIGH"),
    (r"\bdel\s+/[sS]\b|\brd\s+/[sS]\b|\brmdir\s+/[sS]\b",
     "recursive delete (cmd)", "HIGH"),
    (r"\bdd\s+[^\n]*\bof=", "raw block write - can destroy a filesystem", "HIGH"),
    (r"\bmkfs\b|\bFormat-Volume\b|\bformat\s+[a-zA-Z]:", "filesystem format", "HIGH"),
    (r"\bgit\s+clean\b[^\n]*-[a-zA-Z]*[dfx]", "delete untracked/ignored files - not in git history", "HIGH"),
    (r"\bgit\s+reset\b[^\n]*--hard", "discard all uncommitted work in the tree", "MED"),
    (r"\bgit\s+push\b[^\n]*(--force(?!-with-lease)|\s-f\b)",
     "overwrite remote history - can destroy others' commits", "HIGH"),
    (r"\bgit\s+branch\b[^\n]*\s-D\b", "force-delete a branch (unmerged work)", "MED"),
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b|\bTRUNCATE\s+TABLE\b",
     "destroy database objects or all rows", "HIGH"),
    (r"\bDELETE\s+FROM\b(?![^\n]*\bWHERE\b)", "delete every row (no WHERE clause)", "HIGH"),
    (r"\bdocker\s+(system\s+prune|volume\s+(rm|prune)|image\s+prune)",
     "delete docker volumes/images - persisted container data", "HIGH"),
    (r"\bdocker\s+(rm|stop|kill)\s+[^\n]*hermes|hermes-gateway",
     "stop or remove the Hermes gateway container", "MED"),
    (r"\bChoco|\bwinget\s+uninstall|\bapt(-get)?\s+(remove|purge)|\bpip\s+uninstall",
     "uninstall software", "MED"),
    (r"\bSet-ExecutionPolicy\b|\bicacls\b[^\n]*/reset|\btakeown\b",
     "change security or ACL settings", "MED"),
    (r"\bnetsh\b[^\n]*firewall|\bNew-NetFirewallRule\b|\bSet-SmbShare\b|\bNew-SmbShare\b",
     "change firewall or network share exposure", "MED"),
    (r">\s*(?!/dev/null)[^\s|;&]*\.(env|key|pem|yaml|yml|json|md)\b",
     "truncating overwrite of a config/credential/doc file", "MED"),
]
FALLBACK_PROTECTED = [
    (r"(?i)example-vault", "your vault (EDIT to your real path patterns)"),
    (r"(?i)\.ssh[\\/]|id_rsa|\.env\b|\.pem\b", "live credentials / SSH keys"),
    (r"(?i)[\\/]\.claude\b", "harness configuration"),
    (r"(?i)\.rag[\\/]db|chroma|vector-store", "RAG / memory stores (expensive to rebuild)"),
]
FALLBACK_SAFE_PREFIXES = ("git status", "git diff", "git log", "ls", "cat", "echo")
FALLBACK_DISPOSABLE = (
    r"(?i)(AppData[\\/]Local[\\/]Temp|[\\/]tmp[\\/]|scratchpad|node_modules|"
    r"__pycache__|\.pytest_cache|[\\/]dist[\\/]|[\\/]build[\\/])"
)


def load_policy():
    """Return (policy_dict, source_label). Never raises."""
    try:
        with open(POLICY_PATH, encoding="utf-8") as f:
            p = json.load(f)
        # sanity: compiled patterns + required fields, else fall back
        for r in p["rules"]:
            re.compile(r["pattern"])
            _ = r["effect"], r["irreversibility"]
        for r in p["protected"]:
            re.compile(r["pattern"])
        return p, "denylist v%s" % p.get("version", "?")
    except Exception:
        return {
            "safe_prefixes": list(FALLBACK_SAFE_PREFIXES),
            "disposable": FALLBACK_DISPOSABLE,
            "rules": [
                {"pattern": pat, "effect": eff, "irreversibility": lvl, "verdict": "ask"}
                for pat, eff, lvl in FALLBACK_RULES
            ],
            "protected": [
                {"pattern": pat, "label": lab} for pat, lab in FALLBACK_PROTECTED
            ],
            "protected_write": [],
        }, "embedded-fallback (policy JSON unreadable)"


def machine_allows(rule):
    """True if this rule should NOT fire on the current machine."""
    allowed = rule.get("allowed_machines") or []
    if not allowed:
        return False
    return os.environ.get("COMPUTERNAME", "").upper() in [m.upper() for m in allowed]


def analyze_command(command, policy):
    """Return (effects, worst, protected_hits, verdict, deny_msgs) or None."""
    stripped = command.strip()
    # A safe prefix only proves safety when nothing is redirected: 'echo' is
    # benign, 'echo x > config.yaml' truncates the target (found by test R4).
    if stripped.startswith(tuple(policy.get("safe_prefixes", []))) and ">" not in command:
        return None

    effects, worst, verdict, deny_msgs = [], None, "ask", []
    for rule in policy["rules"]:
        if machine_allows(rule):
            continue
        if re.search(rule["pattern"], command):
            effects.append((rule["effect"], rule["irreversibility"]))
            if worst != "HIGH":
                worst = rule["irreversibility"]
            if rule.get("verdict") == "deny":
                verdict = "deny"
                if rule.get("deny_message"):
                    deny_msgs.append(rule["deny_message"])
    if not effects:
        return None

    hits = [p["label"] for p in policy["protected"] if re.search(p["pattern"], command)]
    if hits:
        worst = "HIGH"
    elif re.search(policy["disposable"], command) and verdict != "deny":
        return None  # disposable target, nothing protected named
    return effects, worst, hits, verdict, deny_msgs


def analyze_write(file_path, policy):
    """Return (label, failing) or None for Write/Edit targets."""
    for entry in policy.get("protected_write", []):
        if re.search(entry["pattern"], file_path):
            return entry["label"]
    return None


def emit(decision, reason_lines):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": "\n".join(reason_lines),
        }
    }))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # never break the tool call on a parse failure

    tool = payload.get("tool_name", "")
    policy, source = load_policy()

    if tool in ("Write", "Edit"):
        file_path = payload.get("tool_input", {}).get("file_path", "")
        if not file_path:
            sys.exit(0)
        label = analyze_write(file_path, policy)
        if label is None:
            sys.exit(0)
        emit("ask", [
            "DRY-RUN GATE (%s) - protected write target:" % source,
            "",
            "  - overwrite of: %s  [%s]" % (file_path, label),
            "",
            "  Approve only if this change to a protected file is intended.",
        ])
        sys.exit(0)

    if tool not in ("Bash", "PowerShell"):
        sys.exit(0)

    command = payload.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    result = analyze_command(command, policy)
    if result is None:
        sys.exit(0)

    effects, level, hits, verdict, deny_msgs = result
    lines = ["DRY-RUN GATE (%s) - predicted effects before this runs:" % source, ""]
    for effect, lvl in effects:
        lines.append("  - %s  [%s]" % (effect, lvl))
    if hits:
        lines.append("")
        lines.append("  Touches protected targets:")
        for h in hits:
            lines.append("    * %s" % h)
    lines += ["", "  Irreversibility: %s" % level]
    if verdict == "deny":
        lines += [""]
        for m in deny_msgs:
            lines.append("  BLOCKED: %s" % m)
        emit("deny", lines)
    else:
        lines += [
            "",
            "  HIGH = no undo without an external backup. G: has no local backup.",
            "  Approve only if you have read the command and accept the effects above.",
        ]
        emit("ask", lines)
    sys.exit(0)


if __name__ == "__main__":
    main()
