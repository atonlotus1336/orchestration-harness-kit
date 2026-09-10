# Orchestration Harness Kit

> **Private proprietary distribution.** This repository is not open source and
> is not licensed for public or unauthorized redistribution, whether free or
> paid. Access does not grant redistribution rights. See [`LICENSE`](LICENSE),
> [`PRIVATE-DISTRIBUTION.md`](PRIVATE-DISTRIBUTION.md), and
> [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md). Incorporated third-party
> materials remain governed by their own licenses.

**Make your AI coding agents safer, more reliable, and more capable — one part at a
time.** This kit packages a battle-tested personal stack (persistent memory, a
destructive-command safety gate, reasoning techniques for small local models,
messenger integration, and the operating doctrine that ties it together) so you can
run the same system with **Claude Code, Codex, Hermes/OpenCode, Cursor, or any similar agent**.

It ships the **system, never the author's content**: no memories (your store starts
empty), no private projects, no identity. Machine-specific values are placeholders
(`alice`, `D:\MyVault`, `__STORE__`) you fill in once during install.

The private maintainer repository also carries an optional credential-free owner
snapshot. Client release ZIPs exclude that snapshot and all Git history. It is not
part of core installation or support.

The default topology is **one agentic system on one PC**. That PC owns the
home disk, memory, approvals, and backups. VPS and second-PC connections are
optional modules you enable later; neither becomes a second storage writer.

## Shared authority and work routing

![Nine peer harnesses, including DeepSeek (DSH), share one authority core. T2 executes through governed OMP independently. Deterministic execution evidence feeds one S0-S4 evaluator whose internal controls route, admit budgets, run any required reviewers, and validate results before due goalposts govern completion. Each installation qualifies its own native paths.](assets/harness-fusion-hierarchy.png)

All nine harnesses connect as peers to the shared orchestration core. The left
side shows authority precedence, with evidence supporting factual claims.
The right side shows execution routing, Prime Agent coordinating OMP workers,
and deterministic execution evidence feeding one S0-S4 review evaluator. Its
preflight, budget, invocation, and result-validation controls are internal to
that evaluator. Work continues
until the endpoint passes or a loop limit triggers the escape hatch. Human
verification provides a separate review path.

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
| [`modules/topology/`](modules/topology/) | Solo-PC default plus optional VPS and second-PC role profiles | Expand without changing who owns the home disk or exposing the dashboard |
| [`modules/prime-omp/`](modules/prime-omp/) | A strict contract for letting Prime Agent coordinate oh-my-pi coding workers through Hermes | Add a powerful coding lane without handing workers your real repository or final authority |
| [`modules/token-compute-optimization/`](modules/token-compute-optimization/) | One provider-free budget, depth, cache, rendering, accounting, and rollback profile for every adapter | Reduce wasted context and calls without weakening quality gates or capability discovery |
| [`modules/additive-parallel/`](modules/additive-parallel/) | An inactive-by-default profile for exactly two independent read-only child tasks and Orca observation | Safely accelerate independent inspection or review work without parallel writes or new provider authority |
| [`modules/verified-maintenance/`](modules/verified-maintenance/) | Daily, weekly, and monthly improvement windows that start only after a successful backup | Keep self-improvement recoverable, sandboxed, and human-approved |
| [`system-snapshot/`](system-snapshot/) | The actual sanitized Prime/OMP, Hermes Telegram, auto-learning, autoexperiment, maintenance, backup, and restore implementation | Preserve the recent systemic updates rather than only their portable interfaces |

## Start here: one PC

Run the preflight for your operating system, then render the disabled solo
profile:

```bash
python modules/topology/configure-topology.py --selftest
python modules/topology/configure-topology.py --role solo --output topology-local.json
```

Fill the `<local-value>` entries and verify local services before setting
`enabled` to `true`. Ignore every optional module until the solo core passes.
Small local models should read
[`docs/SMALL-MODEL-START.md`](docs/SMALL-MODEL-START.md) first.

## Install: choose your path

**Path A — cherry-pick one part (recommended first step).** Every part works alone.
Each directory's README tells you how to install *just that part* and how to test it
worked. The safety gate is the best first pick: two files, no dependencies,
30 seconds:

```bash
python modules/guards/test-dry-run-gate.py   # see it pass its own 32 tests first
```

**Path B — the portable SDK.** Follow [`docs/QUICK-START.md`](docs/QUICK-START.md):
dry-run → confirmed install → reference-only harness connect → doctor →
provider-free verify. The same controller performs content-addressed upgrade and
rollback while preserving recipient-local state. Harness activation remains a
separate, explicit deployment decision.

The v2 release-candidate documentation, compatibility matrix, migration path,
adapter-authoring contract, and publication checklist are indexed at
[`docs/v2/README.md`](docs/v2/README.md).

Maintainers may verify the owner snapshot separately using
[`docs/PRIVATE-SYSTEM-SNAPSHOT.md`](docs/PRIVATE-SYSTEM-SNAPSHOT.md). It is not
distributed to clients.

**Independence matrix** — what needs what:

| Part | Depends on | Standalone test |
|---|---|---|
| guards | nothing (Python stdlib) | its self-test, 32 cases |
| reasoning-shapes | nothing (they're documents) | follow one on a hard question |
| messaging | any OpenAI-compatible endpoint | `GATEWAY_URL=... python modules/messaging/generic-adapter.py "hi"` |
| topology | nothing | `python modules/topology/configure-topology.py --selftest` |
| recipe-miner | a harness that saves transcripts | run it on any transcript file |
| docs | nothing | read them |
| system (memory+RAG) | Python 3.11, guide §3 | guide §4 smoke tests |
| hooks | the memory engine | guide §3.4 |
| prime-omp | Hermes plus locally installed Prime Agent and OMP | contract verifier plus harmless patch rehearsal |
| token-compute-optimization | orchestration core plus a recipient-local activation registry | provider-free module verifier plus rollback rehearsal |
| additive-parallel | a recipient-local feature overlay after isolated qualification | provider-free profile and feature-conformance verifiers |

Prime/OMP has two distribution tiers. The allowlisted recipient payload ships the
contract and diagnostic only. Authorized full-Git recipients can restore and
register the bridge by following
[`docs/PRIME-OMP-CLIENT-SETUP.md`](docs/PRIME-OMP-CLIENT-SETUP.md); their machine
must still pass the live Hermes handshake, Prime runtime, and OMP runtime gates.
| verified-maintenance | your backup command and scheduler | schedule verifier plus intentionally failed backup test |
| private system snapshot | Python 3.11 and a destination workspace | hash-verifying restore dry-run |

## Why it's safe to share privately (the 30-second version)

Three deterministic layers keep private data out, none of them "being careful":
an **allowlist** (only named files can enter — a mistake means a missing file, never
a leak), an **identity scrub** (usernames/paths/hosts → placeholders), and a
**tripwire** (build aborts if any private token appears anywhere). Your clone gets a
fourth: run `git config core.hooksPath .githooks` to arm the commit-time leak guard
for your own tokens. These controls prevent data leakage; they do not authorize
public distribution. Follow [`PRIVATE-DISTRIBUTION.md`](PRIVATE-DISTRIBUTION.md)
for every recipient.

## Keeping it current

Use the new release's `python installer/kit.py upgrade --target <install-root>`
flow. It previews by default, snapshots managed state before applying, preserves
local data and harness references, and automatically rolls back a failed update.
See [`docs/UPDATE-AND-REMOVE.md`](docs/UPDATE-AND-REMOVE.md).

No Git on the target? `make-bundle.sh --output orchestration-harness-kit.zip
--apply --yes` produces a scrubbed offline ZIP without Git history or the owner
snapshot.

## What is deliberately NOT here

- **The author's memories, notes, projects, models, or LoRAs** — the boundary is
  structural, not a promise.
- **Skill/agent marketplaces** — install those from their own sources; the
  `system/build-skills-manifest.py` generator indexes whatever skills *you* have.
- **Secrets** — `config.env` is gitignored; every machine fills its own.
- **Runtime state and live evidence** — the private snapshot preserves code and
  policy, while ledgers, logs, signed receipts, caches, and credentials stay local.

## For AI agents and maintainers

If you are an LLM (or a human) developing or updating this kit: **read
[`AGENTS.md`](AGENTS.md) first, then [`KIT-MAINTENANCE.md`](KIT-MAINTENANCE.md)** —
they carry the invariants (naming, scrub discipline, verification pipeline, push
procedure) that keep this repo safe and coherent. The maintainer-side sync/verify
workflow (surface-graph generation, scrub-v2, daily sync loop, one-time repo setup)
lives in KIT-MAINTENANCE.md, not here — downloaders never need it.
