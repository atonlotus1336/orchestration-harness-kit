# installer — the staged full-stack installer

**What it is:** the "all-in" path. Where the per-module READMEs let you cherry-pick
parts, this directory installs the whole stack in verified stages, with a gate
(`gate.ps1`) that checks each stage actually worked before the next one runs —
including proof that a *clean machine* can reproduce the install.

**Why you'd want it:** if you're replicating the complete system (memory + RAG +
doctrine + modules) onto a new machine, staged-with-verification beats "run twenty
commands and hope." Every stage leaves evidence; a failed stage names itself.

## What's inside

- `preflight/` — environment checks (per-OS) that run before anything installs.
- `modules/` — the installable stack pieces, each with its own README:
  - `memory/` — the memory engine (see also `../system/`)
  - `rag/` — RAG backend wiring
  - `harness/` — per-harness (Claude/Codex/Hermes) configuration
  - `hermes-snapshot/` — a *pinned snapshot* of the Hermes agent setup (versioned
    deliberately — it tracks a known-good state, not a moving target)
  - `image-video/` — scaffolding for wiring your own ComfyUI (structure only;
    no models ship in this kit)
  - `module-manifest.json` — what's installable, with per-module exclusions
- `gate.ps1` — the stage-by-stage verifier.
- `clone-eval/` — fixture tasks that prove an installed clone actually *works*
  (can it fix code? operate on files?), not just that files landed.
- `coverage/DECLARED-COVERAGE.md` — honest statement of what the installer does
  and doesn't verify.

## Use

Read `preflight/`, run it for your OS, then work through `modules/` in manifest
order, running `gate.ps1` between stages. Windows-first (PowerShell); the `.sh`
twins cover WSL/Linux paths. Cherry-pickers can ignore this entire directory —
the `modules/*` at repo root install standalone without it.
