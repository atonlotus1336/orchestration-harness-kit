# Orchestration Harness Kit

**Make your AI coding agents safer, more reliable, and more capable — one part at a
time.** This kit packages a battle-tested personal stack (persistent memory, a
destructive-command safety gate, reasoning techniques for small local models,
messenger integration, and the operating doctrine that ties it together) so you can
run the same system with **Claude Code, Codex, Hermes/OpenCode, or any similar agent**.

It ships the **system, never the author's content**: no memories (your store starts
empty), no private projects, no identity. Machine-specific values are placeholders
(`alice`, `D:\MyVault`, `__STORE__`) you fill in once during install.

## What's in the box (plain-language tour)

| Part | What it does | Why you'd want it |
|---|---|---|
| [`modules/guards/`](modules/guards/) | A safety gate that intercepts dangerous commands (recursive deletes, force-pushes, disk formats) **before they run**, predicts their effects, and asks you first — with a hard "no" for command classes that have burned you before | Your agent can never again wreck something in one keystroke |
| [`modules/reasoning-shapes/`](modules/reasoning-shapes/) | Four written reasoning techniques (vote-and-refuse, persona panels, tree search) any model can follow | Makes small local models trustworthy; they refuse instead of confidently guessing |
| [`modules/messaging/`](modules/messaging/) | Connect ANY messenger (Telegram, Discord, Slack, WhatsApp…) to your agent through one tiny contract | Text your AI from your phone, on whatever app you already use |
| [`modules/recipe-miner/`](modules/recipe-miner/) | Watches your finished sessions for repeatable procedures and drafts them as skill candidates — a human always reviews before anything installs | Your skill library grows itself from work you already did |
| [`system/`](system/) | The memory engine (remembers decisions across sessions, hybrid keyword+semantic recall) and the RAG front-door your agents query | Your agent stops forgetting everything between sessions |
| [`docs/`](docs/) | The operating doctrine: how agents should rank evidence vs. memory, route retrieval, and dial reasoning effort — plus the full install guide | The "constitution" that makes multi-agent setups behave consistently |
| [`installer/`](installer/) | Staged zero-to-parity installer with verification gates for the full stack | For going all-in rather than part-by-part |
| [`n8n-integration/`](n8n-integration/) | Templates for wiring an n8n automation server to the stack | If you use n8n for workflows/webhooks |
| [`practice-loop/`](practice-loop/) | A learning loop that turns content you consume into one action, one lesson, one rep — with quality canaries | Stops "watched a tutorial, did nothing" syndrome |
| `hooks/` | Session hooks that write memory digests automatically | Glue between your harness and the memory engine |
| [`tools/`](tools/) | The kit's own build/scrub/verify machinery | Only needed if you maintain or extend the kit |

## Install: choose your path

**Path A — cherry-pick one part (recommended first step).** Every part works alone.
Each directory's README tells you how to install *just that part* and how to test it
worked. The safety gate is the best first pick: two files, no dependencies,
30 seconds:

```bash
python modules/guards/test-dry-run-gate.py   # see it pass its own 32 tests first
```

**Path B — the full stack.** Follow [`docs/REPLICATION-GUIDE.md`](docs/REPLICATION-GUIDE.md)
top to bottom: placeholders → memory engine (empty store) → per-harness wiring →
smoke tests. Then [`REMOTE-ACCESS.md`](REMOTE-ACCESS.md) to reach it from your phone,
and [`modules/messaging/`](modules/messaging/) to text it.

**Independence matrix** — what needs what:

| Part | Depends on | Standalone test |
|---|---|---|
| guards | nothing (Python stdlib) | its self-test, 32 cases |
| reasoning-shapes | nothing (they're documents) | follow one on a hard question |
| messaging | any OpenAI-compatible endpoint | `GATEWAY_URL=... python modules/messaging/generic-adapter.py "hi"` |
| recipe-miner | a harness that saves transcripts | run it on any transcript file |
| docs | nothing | read them |
| system (memory+RAG) | Python 3.11, guide §3 | guide §4 smoke tests |
| hooks | the memory engine | guide §3.4 |

## Why it's safe to share (the 30-second version)

Three deterministic layers keep private data out, none of them "being careful":
an **allowlist** (only named files can enter — a mistake means a missing file, never
a leak), an **identity scrub** (usernames/paths/hosts → placeholders), and a
**tripwire** (build aborts if any private token appears anywhere). Your clone gets a
fourth: run `git config core.hooksPath .githooks` to arm the commit-time leak guard
for your own tokens.

## Keeping it current

```bash
git pull        # that's it — updates arrive as commits
```

No git on the target? `bash make-bundle.sh` produces an offline zip of the committed
state.

## What is deliberately NOT here

- **The author's memories, notes, projects, models, or LoRAs** — the boundary is
  structural, not a promise.
- **Skill/agent marketplaces** — install those from their own sources; the
  `system/build-skills-manifest.py` generator indexes whatever skills *you* have.
- **Secrets** — `config.env` is gitignored; every machine fills its own.

## For AI agents and maintainers

If you are an LLM (or a human) developing or updating this kit: **read
[`AGENTS.md`](AGENTS.md) first, then [`KIT-MAINTENANCE.md`](KIT-MAINTENANCE.md)** —
they carry the invariants (naming, scrub discipline, verification pipeline, push
procedure) that keep this repo safe and coherent. The maintainer-side sync/verify
workflow (surface-graph generation, scrub-v2, daily sync loop, one-time repo setup)
lives in KIT-MAINTENANCE.md, not here — downloaders never need it.
