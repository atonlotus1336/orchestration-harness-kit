# Orchestration Layer — shareable, version-controlled

The **system** behind a multi-LLM (Claude Code + Codex + Hermes) stack: a persistent
memory engine, a RAG front-door MCP, the authority/retrieval/method doctrine, and the
skills-index generator. This repo is the **single shareable source of truth** — track it
in git so another machine or deploy gets your latest with `git pull`, not a stale zip.

It deliberately ships the **system, not the content**: no memories/data (the store installs
empty), no `Projects/`, no proprietary skill bundles, no identity. Everything machine-specific
is a placeholder (`alice`, `/mnt/d/MyVault`, `D:\MyVault`, `__STORE__`) you fill in once.

## What's here
```
system/   memoryweaver/ (engine) · memoryweaver-mcp/server.py · rag-orchestrator/ · tests/ · build-skills-manifest.py
docs/     AUTHORITY-CONSTITUTION · METHODOLOGY-CHARTER · RETRIEVAL-PLAYBOOK · REASONING-EFFORT
          · LLM-OPS-RUNBOOK · ORCHESTRATION-LAYER · CONNECTOR · REPLICATION-GUIDE
sync-shareable.sh   populate/refresh this repo from the live vault (allowlist + scrub + tripwire)
make-bundle.sh      produce an offline zip/tar.gz from the committed state (git archive)
.githooks/pre-commit  leak guard — blocks committing identity / host paths / Projects
```

## The boundary (why it's safe to share)
1. **Allowlist-in** — `sync-shareable.sh` copies only the named infra files. Nothing else can enter.
2. **Identity scrub** — username, vault name, site, IP, and home/vault paths → generic placeholders (case-insensitive).
3. **Tripwire** — the sync **and** the pre-commit hook abort on any token in `.private-denylist` or a `Projects/` path.

> **Local-only (gitignored, never in a clone):** `sync-shareable.sh` (carries the real scrub map)
> and `.private-denylist` (your real identity/host tokens). The tracked files carry no identity —
> that's what makes the repo safe to publish. A recipient adds their own `.private-denylist`.

## Daily workflow (keep the share current as you tinker)
```bash
# on the source machine, after editing the live vault:
bash sync-shareable.sh        # refresh + scrub + tripwire  (re-runnable, idempotent)
git diff                      # review what changed
git add -A && git commit -m "sync: <what changed>"
git push                      # once you've added a remote (see below)
```
> Tip: to keep the share near-continuous, call `sync-shareable.sh` from your Stop hook or a
> daily cron, then commit. The repo then always trails the live vault by one commit.

## Get the latest on another machine / a website deploy
```bash
git clone <your-remote> orchestration-layer    # first time
git pull                                        # every update — this is the "carry my changes" answer
```
Then install: follow `docs/REPLICATION-GUIDE.md` (fills the placeholders, wires MCP/RAG per harness,
installs the memory engine with an empty store). The engine + `server.py` use `__STORE__` / `__FTS_DB__`
placeholders the guide substitutes.

## Offline / air-gapped transfer (no git on the target)
```bash
bash make-bundle.sh           # ../orchestration-layer-bundle.{zip,tar.gz} from the committed HEAD
```
Same scrubbed content, just frozen. Rebuild it any time the repo advances.

## What is NOT here (replicate separately, by design)
- **Skills & agents** — a mix of third-party marketplace installs and your proprietary sets;
  install the marketplaces + your private skills on the target. The generator (`build-skills-manifest.py`)
  rebuilds `MASTER-INDEX`/`SKILLS.json` against whatever skills the target has.
- **Memories / notes / decision journals / project data** — machine-local and private; never travels.
- **Secrets** — live in `config.env` (gitignored), filled per machine.

## One-time setup of THIS repo
```bash
git init
git config core.hooksPath .githooks      # turn on the leak guard
git add -A && git commit -m "initial shareable orchestration layer"
# git remote add origin <url> && git push -u origin main   # when you're ready to publish
```
