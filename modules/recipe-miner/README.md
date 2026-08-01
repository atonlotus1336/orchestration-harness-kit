# recipe-miner — your skill library grows itself

**What it is:** a script that reads a finished agent session transcript and looks
for repeatable procedures — sequences of 3–6 meaningful commands that worked. When
it finds one, it drafts a *candidate* skill file into a staging folder.

**Why you'd want it:** you solve the same classes of problem repeatedly. Without
mining, that experience evaporates when the session ends; with it, every solved
problem is a potential reusable skill.

**The safety property (the important part):** the miner **never installs
anything**. It stages drafts; a human reviews and promotes them (or rejects them —
and a hash ledger remembers rejections so the same candidate is never proposed
twice). An agent that could teach itself skills unsupervised would be a hazard;
this one can only *suggest*.

## Install just this part

1. Copy `recipe-miner.py` anywhere and set two environment variables:
   - `SKILL_STAGING` — where candidate drafts land (any empty folder)
   - `SKILLS_STORE` — your existing skills folder (for duplicate detection)
2. Run it manually on any transcript to see what it finds:
   ```bash
   python recipe-miner.py /path/to/session-transcript.jsonl
   ```
3. To automate (Claude Code): register it as a `SessionEnd` hook in
   `settings.json` — it then quietly mines every session you finish.
4. Personalize the `GENERIC_TOKENS` list in the script (marked with an EDIT
   comment): add your own vault/user words so path fragments never become
   "procedure themes."

Review staged candidates on your own schedule; a candidate is just a markdown
draft until *you* decide it becomes a skill.
