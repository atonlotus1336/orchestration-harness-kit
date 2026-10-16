# guards — the destructive-command safety gate

**What it is:** a small Python hook that intercepts dangerous commands *before your
AI agent runs them*. It recognizes destructive patterns (recursive deletes, disk
formats, force-pushes, database drops…), predicts what would happen, and asks you
to approve — or flat-out refuses command classes you've marked as forbidden.

**Why you'd want it:** one approved-too-fast keystroke can delete a vault with no
backup. This gate makes that structurally impossible instead of "hopefully caught."

## The three files

- `destructive-patterns.json` — the policy. 19 ready-made rules plus an example
  showing how to add your own "never again" rules (each cites the incident behind it).
- `dry-run-gate.py` — the hook. Reads the policy, checks every shell command and
  file write, stays silent for safe ones, asks/denies for dangerous ones.
- `test-dry-run-gate.py` — the proof. 67 cases: every rule fires on its named
  trigger, safe commands pass untouched, read-only or document-only
  commands that merely *mention* a dangerous string stay silent (policy
  `1.1.0-template` + hook v3, 2026-10-15), and `MultiEdit` / `NotebookEdit`
  targets get the same protected-write check as `Write` / `Edit`.

## Install just this part (Claude Code, ~2 minutes)

1. Copy this folder anywhere (e.g. `~/.claude/guards/`).
2. Run the self-test — it must pass before you trust it:
   ```bash
   python test-dry-run-gate.py
   ```
3. Register the hook in your `~/.claude/settings.json`:
   ```json
   "hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit",
     "hooks": [{"type": "command", "command": "python /path/to/dry-run-gate.py", "timeout": 10}]}]}
   ```
4. Open a NEW session (hooks load at startup) and try `rm -rf something-precious` —
   you'll see the gate's prediction and an approval prompt.

**Then personalize:** edit the `example-vault` patterns in the JSON to your real
protected paths, and re-run the self-test.

Other harnesses (Codex, Hermes) can't intercept mechanically — for them, add a
standing rule to consult the policy before destructive commands (see the kit's
AGENTS.md pattern). Advisory is weaker than mechanical; the file is the same.
