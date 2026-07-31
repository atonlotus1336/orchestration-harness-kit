#!/usr/bin/env python
"""
Recipe miner (agentic-patterns phase 5) — agent-workflow-memory pattern.

Scans a Claude Code transcript JSONL for repeatable 3-6 step shell
procedures and stages DRAFT candidates for human review. It NEVER installs
a skill: skill-catalog-review is the only intake gate.

Invocation:
  as SessionEnd hook : reads the hook envelope on stdin (transcript_path)
  manually           : python recipe-miner.py <transcript.jsonl>

Outputs (only writes under skills-staging\\):
  <staging>/<slug>/CANDIDATE.md   draft, NOT a SKILL.md
  <staging>/_mined-ledger.json    hash ledger (staged|rejected)
  (staging root: $SKILL_STAGING; skill store: $SKILLS_STORE)
"""
import hashlib
import json
import os
import re
import sys
from datetime import date

STAGING = os.environ.get("SKILL_STAGING", "/data/example-vault/skills-staging")
LEDGER = os.path.join(STAGING, "_mined-ledger.json")
SKILLS_SHARED = os.environ.get("SKILLS_STORE", "/data/example-vault/skills-shared")

TRIVIAL = re.compile(
    r"^\s*(ls|cat|echo|pwd|cd|head|tail|git\s+(status|diff|log)|Get-ChildItem|"
    r"Get-Content|Test-Path|grep|sed\s+-n|python\s+--version|which)\b"
)
GENERIC_TOKENS = {
    "python", "powershell", "bash", "echo", "json", "file", "true", "false",
    "noprofile", "executionpolicy", "bypass", "null", "head", "grep", "http",
    "https", "com", "www", "exe", "print", "import", "open", "the", "and",
    # vault/path fragments — never a real procedure theme
    "myvault", "claude", "users", "alice", "mnt", "temp",  # EDIT: add YOUR vault/user tokens
    "appdata", "local", "planning", "scripts", "skills", "hooks", "guards",
    "codex", "projects", "phases", "shared",
}
MIN_RUN, MAX_RUN = 3, 6


def load_ledger():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def extract_commands(transcript_path):
    """Yield (index, command) for each Bash/PowerShell tool_use, in order."""
    out = []
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            msg = entry.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") in ("Bash", "PowerShell")):
                    cmd = (block.get("input") or {}).get("command", "")
                    if cmd and not TRIVIAL.match(cmd):
                        out.append(cmd)
    return out


def theme_token(commands):
    """Most frequent non-generic token appearing in >= MIN_RUN commands."""
    counts = {}
    for cmd in commands:
        seen = set()
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}", cmd):
            t = tok.lower()
            if t in GENERIC_TOKENS or t in seen:
                continue
            seen.add(t)
            counts[t] = counts.get(t, 0) + 1
    best = [t for t, c in counts.items() if c >= MIN_RUN]
    return max(best, key=lambda t: counts[t]) if best else None


def existing_skill_covers(token):
    try:
        for d in os.listdir(SKILLS_SHARED):
            if token in d.lower() or d.lower() in token:
                return d
    except Exception:
        pass
    return None


def mine(commands):
    """Sliding window: runs of MIN..MAX consecutive commands with a theme."""
    candidates = []
    i = 0
    while i < len(commands):
        window = commands[i:i + MAX_RUN]
        if len(window) < MIN_RUN:
            break
        token = theme_token(window)
        if token:
            run = [c for c in window if token in c.lower()]
            if len(run) >= MIN_RUN:
                candidates.append((token, run[:MAX_RUN]))
                i += MAX_RUN
                continue
        i += 1
    return candidates


def stage(token, run, source, ledger):
    seq_hash = hashlib.sha256("\n".join(run).encode()).hexdigest()[:16]
    if seq_hash in ledger:
        return None  # already staged or rejected — never re-propose (R12)
    covered = existing_skill_covers(token)
    if covered:
        ledger[seq_hash] = {"status": "skipped-existing", "skill": covered, "token": token}
        return None
    slug = "%s-%s" % (re.sub(r"[^a-z0-9]+", "-", token)[:30], date.today().strftime("%m%d"))
    cdir = os.path.join(STAGING, slug)
    os.makedirs(cdir, exist_ok=True)
    lines = [
        "# CANDIDATE (mined draft — NOT an installed skill)",
        "",
        "status: awaiting skill-catalog-review",
        "mined: %s" % date.today().isoformat(),
        "source-transcript: %s" % source,
        "sequence-hash: %s" % seq_hash,
        "theme: %s" % token,
        "",
        "## Observed procedure (%d steps)" % len(run),
        "",
    ]
    for n, cmd in enumerate(run, 1):
        lines += ["%d." % n, "```", cmd.strip()[:500], "```", ""]
    lines += [
        "## Review checklist (skill-catalog-review)",
        "- [ ] Is this genuinely repeatable (not one-off session plumbing)?",
        "- [ ] Does an existing skill already cover it?",
        "- [ ] Rewrite as a proper SKILL.md with When-to-use + failure notes",
        "- [ ] Reject: move this entry to status 'rejected' with a reason in _mined-ledger.json",
    ]
    with open(os.path.join(cdir, "CANDIDATE.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    ledger[seq_hash] = {"status": "staged", "slug": slug, "token": token}
    return slug


def main():
    # transcript path: argv, or SessionEnd hook envelope on stdin
    if len(sys.argv) > 1:
        transcript = sys.argv[1]
    else:
        try:
            transcript = json.load(sys.stdin).get("transcript_path", "")
        except Exception:
            sys.exit(0)
    if not transcript or not os.path.isfile(transcript):
        sys.exit(0)
    if not os.path.isdir(os.path.dirname(STAGING)):
        sys.exit(0)  # staging volume unmounted — silent no-op, never an error

    os.makedirs(STAGING, exist_ok=True)
    ledger = load_ledger()
    staged = []
    for token, run in mine(extract_commands(transcript)):
        slug = stage(token, run, transcript, ledger)
        if slug:
            staged.append(slug)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1)
    if staged:
        print("recipe-miner: staged %s -> %s" % (", ".join(staged), STAGING))
    sys.exit(0)


if __name__ == "__main__":
    main()
