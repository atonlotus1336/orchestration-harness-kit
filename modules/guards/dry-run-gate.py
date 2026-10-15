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

v3 (2026-10-15): policy 1.1.0 precision fixes, plus strip_inert_text():
  heredoc bodies written to plain documents and read-only search segments
  are not scanned (both rules fail toward firing; see the v3 note below),
  and a safe prefix no longer exempts a chained, piped or heredoc command.
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
    (r"\bdocker\s+(?:container\s+|compose\s+(?:[^\n|;&]*?\s)?)?(?:rm|stop|kill|restart|down)\b[^\n]*\bhermes",
     "stop or remove the Hermes gateway container", "MED"),
    (r"\bChoco|\bwinget\s+uninstall|\bapt(-get)?\s+(remove|purge)|\bpip\s+uninstall",
     "uninstall software", "MED"),
    (r"\bSet-ExecutionPolicy\b|\bicacls\b[^\n]*/reset|\btakeown\b",
     "change security or ACL settings", "MED"),
    (r"\bnetsh\b[^\n]*firewall|\bNew-NetFirewallRule\b|\bSet-SmbShare\b|\bNew-SmbShare\b",
     "change firewall or network share exposure", "MED"),
    (r"(?<![>=\-])>(?![>&])\s*(?!/dev/null)"
     r"(?![^\s|;&]*(?i:[\\/]tmp[\\/]|[\\/]temp[\\/]|scratchpad|\$TMPDIR|\$env:TEMP|%TEMP%))"
     r"[^\s|;&]*\.(env|key|pem|yaml|yml|json|md)\b",
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


# --- v3 (2026-10-15): ignore text that cannot execute -----------------------
# Only two kinds of text are treated as inert, and both fail toward FIRING:
#   1. A heredoc body fed by plain `cat`/`tee` (no pipe) into document files
#      only. A body piped to a shell, fed to an interpreter, or written to a
#      script/config file is still scanned, because it can run later.
#   2. A read-only search segment (grep/rg/findstr/Select-String...) with no
#      redirect, command substitution, process substitution or --pre hook.
# Anything ambiguous stays scanned. Split points inside quotes over-split,
# which only ever exposes MORE text to the rules.
DOC_EXTS = (".md", ".markdown", ".txt", ".rst", ".html", ".csv", ".log")
SEARCH_TOOLS = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr",
                "select-string", "sls"}
HEREDOC_RE = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
SEGMENT_SPLIT = re.compile(r"(\n|&&|\|\||;|\|)")
PREFIX_WORD = re.compile(r"^(sudo|command|timeout|\d+[smh]?|[A-Za-z_]\w*=\S*)$")


def _lead_words(segment):
    words = segment.strip().split()
    while words and PREFIX_WORD.match(words[0]):
        words = words[1:]
    return words


def _heredoc_feeds_document(header_line):
    """True when the heredoc on this line only feeds cat/tee into documents."""
    seg = next((s for s in re.split(r"&&|\|\||;", header_line) if "<<" in s), "")
    if "|" in seg:
        return False
    words = _lead_words(seg)
    if not words or words[0] not in ("cat", "tee"):
        return False
    if words[0] == "cat":
        targets = re.findall(r">>?\s*([^\s<>]+)", seg)
    else:
        pre = seg.split("<<")[0].split()
        after = pre[pre.index("tee") + 1:] if "tee" in pre else []
        targets = [t for t in after if not t.startswith("-") and ">" not in t]
    targets = [t.strip("'\"") for t in targets]
    return bool(targets) and all(t.lower().endswith(DOC_EXTS) for t in targets)


def _is_search_segment(segment):
    # discarding output (2>/dev/null, 2>&1, 2>$null) is not a write
    probe = re.sub(r"\d?>\s*/dev/null|\d?>&\d|\d?>\s*\$null", "", segment)
    if any(tok in probe for tok in ("$(", "`", "<(", ">", "--pre")):
        return False
    words = _lead_words(segment)
    return bool(words) and words[0].lower() in SEARCH_TOOLS


def strip_inert_text(command):
    """Blank text that cannot execute; see the v3 note above."""
    lines, out, i = command.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = HEREDOC_RE.search(line)
        if not m:
            continue
        delim, tab_strip = m.group(3), m.group(1) == "-"
        inert = _heredoc_feeds_document(line)
        while i < len(lines):
            body = lines[i]
            # lenient end match ends the body early = scans more, never less
            end = (body.lstrip("\t") if tab_strip else body).strip() == delim
            out.append(body if (end or not inert) else "")
            i += 1
            if end:
                break
    parts = SEGMENT_SPLIT.split("\n".join(out))
    for k in range(0, len(parts), 2):
        if _is_search_segment(parts[k]):
            parts[k] = ""
    return "".join(parts)


def analyze_command(command, policy):
    """Return (effects, worst, protected_hits, verdict, deny_msgs) or None."""
    stripped = command.strip()
    # A safe prefix only proves safety for ONE simple command: 'echo' is
    # benign, 'echo x > config.yaml' truncates the target (found by test R4),
    # and 'ls && rm -rf ...' or 'cat <<EOF | bash' chain a second command
    # (found 2026-10-15). Any redirect, chain, pipe, heredoc, newline or
    # substitution disables the shortcut.
    if (stripped.startswith(tuple(policy.get("safe_prefixes", [])))
            and not re.search(r"[>;|&\n`]|\$\(|<<", command)):
        return None

    command = strip_inert_text(command)
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
