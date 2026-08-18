# AGENTS.md — instructions for any LLM working on this repository

You are working on a **portable agentic-harness kit**: memory engine, RAG
front-door, doctrine docs, safety gate, reasoning shapes, recipe miner,
messenger adapters, and the privacy pipeline that keeps personal data out of
all of it. Recipients wire it into Claude Code, Codex, Hermes, or any
OpenAI-compatible harness.

**Before ANY change, read `KIT-MAINTENANCE.md`** — it is the update and push
procedure (both maintenance lanes, the scan gates, the hard rules). This file
covers what it doesn't: how to DEVELOP the kit well.

## Architecture in one paragraph

Reliability lives in FILES, not in models. Doctrine docs set precedence
(evidence over narration), method skills carry procedures any model can
follow, gates make irreversible calls deterministically, and the manifest +
scrub + tripwire pipeline makes publishing fail-closed. When you add
capability, add it as a file a weak model could follow cold — never as
behavior that only works with a frontier model driving.

## Design invariants (violating these is a bug, not a style choice)

1. **Self-testing or it doesn't ship.** Every gate/rule/module carries a
   named failing input and a runnable check that replays it. A gate that
   cannot name an input that would make it FAIL is void.
2. **Deny-by-default at every boundary** (senders, machines, commands), and
   deny is evaluated BEFORE any allow exemption.
3. **Ship the port, not the plug.** Integrations target open contracts
   (OpenAI-compatible API, webhook JSON) — never a named vendor in core.
4. **Boolean flags, never numeric rubrics** in any LLM-evaluated check
   (small models flatten 1–5 scales to the band middle).
5. **Paper state.** Multi-step procedures keep bookkeeping in files, not in
   context (see modules/reasoning-shapes — state lives in `.reasoning-runs/`).
6. **Placeholders only.** `alice`, `example-vault`, `D:\MyVault`,
   `example.com`. No real usernames, hosts, IPs, or keys — even in examples.
7. **Stage, never auto-install.** Anything that generates skills/config for
   the user's system writes to a staging area for human review.

## Prime/OMP diagnostic authority

- Do not use the current chat's tool list or `tool_search` as proof that Prime
  is unavailable. Test the configured MCP server with `hermes --profile
  <profile> mcp test prime-agent`. A successful handshake plus missing session
  tools means the session snapshot is stale. Reload MCP and open a new session.
- Do not treat `omp-runtime-lock.json` as live status. It records provenance
  and pins. Run the installation's canonical OMP feasibility probe instead.
- OMP is an executable worker governed through Prime. It is not merely an
  advisory policy, and an advisory recommendation must never bypass its
  feasibility, capability, or human-approval gates.
- GSD/Ponytail routing is an orchestration decision. A missing model-visible
  Prime tool is a connector/session fault, not a routing verdict.

## Recipe: adding a new module

1. Create `modules/<name>/` with: the artifact(s), a README section or
   contract doc, and a self-test that passes OUT OF THE BOX against shipped
   defaults (see `modules/guards/test-dry-run-gate.py` as the model).
2. Check the module NAME itself against private-token policies — module
   names have been leaks before (a module shipped renamed for this reason).
3. Verify `python -m py_compile` on any Python; run the module's self-test.
4. Then follow KIT-MAINTENANCE.md to register, scan, commit, push.
   NEVER commit with --no-verify; a blocked commit is the system working.

## Current roadmap (update this list when you change it)

- **Role-aware topology (v2 portable core built):** `modules/topology` makes
  solo single-PC the default role and provides opt-in VPS/second-PC profiles,
  with Tailscale as recommended
  (not required) transport; per-machine identity file + service-address
  indirection so scaling out is a config edit, not a re-architecture.
  Bind policy: chat/API surfaces may bind wide with auth; admin/dashboard
  surfaces stay loopback + tunnel. Verification probes must run FROM the
  remote machine (local green proves nothing about remote reach).
  `REMOTE-ACCESS.md` supplies the network procedure; the topology module
  renders disabled local role files and verifies the storage/bind contract.
- **Docs re-curation:** `docs/` copies lag their upstream originals; they
  are curated per-file, not auto-synced.
- **Messaging:** more reference adapters welcome (Discord/Slack examples)
  but only as templates with credentials wired externally.

## What this repo must NEVER contain

Real credentials/keys/tokens; real hostnames, tailnet or LAN IPs; personal
memory stores or their contents; project/vault content from any source
system; consent-governed community data (structural exclusion, not policy).
If you find any of the above already present: stop, do not push, surface it.
