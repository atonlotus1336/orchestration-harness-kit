# Kit Maintenance — instructions for any LLM (or human) updating this repo

This file is the standing procedure for pushing updates. It is written to be
followed by a language model with shell access. Follow it literally; the
gates are deterministic and each one exists because of a real leak it caught.

## The one rule that governs everything

**Every publish channel scans its own payload.** A GREEN preview does NOT
certify a commit; a clean commit does NOT certify the preview. The preview
scans sanitized COPIES of allowlisted files; git ships RAW tracked bytes.
Different file sets, different content. Run BOTH gates, always.

## Two maintenance lanes

### Lane A — upstream author (the system this kit was exported from)

The kit is manifest-driven: files enter via rows in the author's surface
registry (privacy_class governs export; kit-component rows point at tracked
kit files). The author-side loop:

1. Add/edit the file inside the kit working tree.
2. Register any NEW file as a `kit-component` row (privacy_class: shareable,
   Test-Path probe) in the surface registry.
3. Regenerate the allowlist: `tools/m10-generate-from-surface-graph.ps1`
   → expect `"status": "GREEN"` with the new row count.
4. Run the sync + scrub: `sync-shareable-v2.ps1 -RequireSanitizerRules`
   → expect scrub `"hit_count": 0`. A RED lists exact file:line:token hits.
5. Fix REDs at the SOURCE when possible (reword to avoid the token class);
   use sanitizer substitution rules only when the replacement lands on an
   APPROVED token — scanners match literal bytes, so an escaped variant
   (a JSON-escaped Windows path) needs its own approved form.
6. `git add -A && git commit` — the pre-commit tripwire re-scans the staged
   payload against the private denylist. It has caught tokens the preview
   scan structurally cannot see (untracked-at-sync files, case variants).
   When the guards suite or a doc that states its test count is staged, the
   hook also runs `tools/check-doc-counts.py` against the staged copies and
   blocks a stale count.
7. `git push` (remote is a PRIVATE repo until a deliberate publish decision).

### Lane B — recipient maintaining their own fork

You have no surface registry; your gates are (a) the pre-commit tripwire in
`.githooks/` (activate: `git config core.hooksPath .githooks`) and (b) your
own local, GITIGNORED policy files, which you create once:
- `.private-denylist` — YOUR identity tokens (username, hostnames, IPs,
  project names), one per line, regex ok.
- `.sanitize-rules.local.json` — `[{"From":"<real>","To":"<placeholder>"}]`.
- `approved-public-tokens.txt` — placeholder vocabulary allowed to ship.
Then the loop is: edit → commit (tripwire) → push.

On a fresh clone, initialize both ignored policy files without printing their
contents:

```bash
python tools/init-maintainer-policy.py --root . --apply
```

The command refuses to overwrite an existing policy unless `--force` is
explicitly supplied. Review the files locally before the first commit.

## Hard rules (do not negotiate these away)

- **Commit metadata is a publish channel no scanner checks.** Author/committer
  name+email ship with every push, and git inherits them from the machine's
  GLOBAL config — which may be a different identity than this repo should
  carry. Before the first commit in any clone:
  `git config user.name <name> && git config user.email <email>` (repo-local).
  If wrong identities already landed: rewrite history (filter-branch/
  filter-repo env-filter) and `push --force-with-lease` — acceptable on a
  private repo with no other consumers; after any history rewrite,
  `git fetch` first or the lease check rejects with "stale info".
- NEVER commit with `--no-verify`. A blocked commit is the system working.
- **A staged-file scanner cannot find pre-existing leaks.** `.githooks/pre-commit`
  reads `git diff --cached` — only files you touched. Anything committed BEFORE
  a token joined `.private-denylist` is never re-read, so it survives forever
  while every gate reports clean. Proven 2026-08-01: two project-name leaks
  survived four scrub passes in untouched files. After ANY denylist addition,
  and periodically, run `bash tools/scan-full-repo.sh` (reads every tracked
  file). Adding a token without a full-repo rescan is a no-op for past commits.
- NEVER put credentials, API keys, tokens, or real hostnames/IPs in ANY
  tracked file — including inside "examples". Use the approved placeholder
  vocabulary (`alice`, `example-vault`, `D:\MyVault`, `example.com`).
- A RED gate is fixed at the source or the fix lands on approved tokens.
  Do not widen the approved list to silence a hit unless a human owner of
  the repo has reviewed that exact token.
- Modules must stay self-testing: if you add a gate/rule, it ships with a
  named failing input and a test that replays it (see `modules/guards/`
  — a gate that cannot name an input that would make it fail is void).
- Messenger/integration modules never hardcode a vendor: ship the port
  (OpenAI-compatible contract), not the plug (see `modules/messaging/`).
- **Every part directory carries a user-facing README** (convention since
  2026-07-31): plain language, four answers — what it is, why you'd want it,
  how to install JUST this part, how to test it worked. Written for a
  non-professional developer; jargon (MCP, RAG, hooks) gets a one-clause
  gloss on first use. The top-level README is the friendly tour + the
  independence matrix; maintainer mechanics live HERE, never there. A new
  module without its README (and its graph row) is incomplete.
- Public vendor domains in docs (n8n.io, github.com) go on
  `approved-public-tokens.txt` — exact token per line, human-reviewed, same
  rule as any approved-list widening.
- **Procedural docs and error messages pass the slop linter before shipping**
  (convention since 2026-08-01): run
  `python modules/writing-standard/slop-lint.py --strict <file>` on runbook
  steps, install procedures, and error/deny text; fix flagged lines. READMEs
  are EXEMPT by name — they are voiced welcome surfaces, and the standard's
  own scope rule forbids applying it to voice. The linter fixes form, not
  truth; it never replaces the doc-accuracy checks.

## Verify after any update (out-of-the-box checks a fresh clone must pass)

```bash
python modules/guards/test-dry-run-gate.py          # expect: 67 passed, 0 failed
python tools/check-doc-counts.py                    # expect: doc counts match (fails on a stale count in any doc)
python tools/check-doc-counts.py --selftest         # expect: selftest passed
python -m py_compile modules/recipe-miner/recipe-miner.py modules/messaging/generic-adapter.py
# adapter live-check against any OpenAI-compatible backend you run:
GATEWAY_URL=http://127.0.0.1:11434/v1 MODEL=<model> \
  python modules/messaging/generic-adapter.py "Reply with exactly: ADAPTER-OK"
```
