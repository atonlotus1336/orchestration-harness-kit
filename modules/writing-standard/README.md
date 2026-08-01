# writing-standard — docs that can't be misread

**What it is:** a two-mode writing system (derived from ASD-STE100, the
aerospace "Simplified Technical English" standard) plus a zero-cost linter
that checks compliance mechanically. AI "slop" — hedging, synonym rotation,
marketing adjectives, run-ons — is not a feeling; it's six nameable habits a
script can count. This module names them, bans them where misreading has a
cost, and proves the result with a number.

**Why you'd want it:** procedures and error messages get read by stressed
humans and small local models. Ambiguity there isn't a style problem — it's
the 2am-incident problem. Measured effect: loading a real writing system cuts
slop 50–74% versus banning words one at a time (~3%). And the governed text
gets ~40% shorter while losing zero information — cheaper to read forever.

## The two files

- `writing-standard.SKILL.md` — the rules. STRICT mode for procedures/error
  messages (hard length caps, one meaning per word, active voice); FLAVORED
  mode for general doc prose. Explicitly EXEMPT: READMEs, marketing,
  anything with a voice — this is a torque wrench, not a poetry editor.
- `slop-lint.py` — the checker. Deterministic Python, no LLM, no
  dependencies. Scores violations-per-100-words with line-numbered flags;
  markdown-aware (code blocks aren't prose); `--max` makes it a pass/fail
  gate; `--selftest` proves every rule on named inputs before it judges
  anything.

## Install just this part

1. Prove the checker first:
   ```bash
   python slop-lint.py --selftest
   ```
2. Put the SKILL.md wherever your harness loads skills; invoke it for any
   doc/procedure/error-message writing task.
3. Lint what the model wrote:
   ```bash
   python slop-lint.py --strict path/to/runbook.md --max 1.0
   ```
   Fix only the flagged lines; rerun. The exit code makes it CI/hook-friendly.

**Honest limit:** this fixes form, not substance — it can polish a hollow
paragraph into a clean hollow paragraph. Pair it with a truth-checker (see
the doc-drift-audit pattern in `docs/`) for the other half.
